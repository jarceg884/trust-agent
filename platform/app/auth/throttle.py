"""Login rate limiting persisted in `ops.login_throttle` (REQ-SEC-006, D-DB-14).

The counting is PRE-authentication and cross-tenant by nature: there is no
trusted tenant_id yet, so `audit_log` cannot be used (it requires a reliable
tenant and is append-only per tenant) and business tables are behind RLS.
The counters therefore live in the provider plane `ops` (no RLS by design,
D-DB-01) — NOT in a SECURITY DEFINER function (owner-privilege surface this
schema deliberately avoids, same criterion as D-CP-10 for job claims).

Two key families:
  - account: "acct:<tenant_id>:<sha256(sub)>" — built ONLY from claims that
    verified cryptographically (a forged token cannot steer someone else's
    counter; at worst it burns its own IP counter).
  - ip: "ip:<inet>" — counted for every failure; covers garbage-token floods.

Progressive lockout: after `login_max_failures` failures inside the sliding
window, `locked_until = now + base * 2^(fail_count - max)` (capped). The
counter lives in PostgreSQL, so it survives an API restart (AC-SEC-006).
"""
import hashlib
import math

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings
from ..errors import rate_limited

# Single-statement upsert: the window/lock arithmetic runs inside PostgreSQL
# so two concurrent failures cannot read-modify-write past each other (the
# FOR UPDATE in the CTE serializes same-key attempts; ON CONFLICT absorbs the
# insert race).
_REGISTER_FAILURE_SQL = text(
    """
    WITH cur AS (
      SELECT fail_count, last_fail_at, window_started_at
        FROM ops.login_throttle WHERE key = :key FOR UPDATE
    ), next AS (
      SELECT CASE
               WHEN cur.last_fail_at IS NULL
                    OR cur.last_fail_at < now() - make_interval(secs => :window_secs)
               THEN 1 ELSE cur.fail_count + 1
             END AS fail_count,
             CASE
               WHEN cur.last_fail_at IS NULL
                    OR cur.last_fail_at < now() - make_interval(secs => :window_secs)
               THEN now() ELSE cur.window_started_at
             END AS window_started_at
        FROM cur
    )
    INSERT INTO ops.login_throttle
      (key, fail_count, window_started_at, last_fail_at, locked_until, updated_at)
    SELECT :key,
           COALESCE(next.fail_count, 1),
           COALESCE(next.window_started_at, now()),
           now(),
           CASE WHEN COALESCE(next.fail_count, 1) >= :max_fails
                THEN now() + make_interval(secs => LEAST(
                       :base_secs * power(2, COALESCE(next.fail_count, 1) - :max_fails),
                       :max_secs)::int)
                ELSE NULL END,
           now()
      FROM (SELECT 1) AS seed
      LEFT JOIN next ON true
    ON CONFLICT (key) DO UPDATE SET
      fail_count       = EXCLUDED.fail_count,
      window_started_at = EXCLUDED.window_started_at,
      last_fail_at     = EXCLUDED.last_fail_at,
      locked_until     = EXCLUDED.locked_until,
      updated_at       = now()
    """
)


def account_key(tenant_id: str, sub: str) -> str:
    """Per-account counter key. The subject is stored hashed (provider plane
    table, no tenant context — minimize PII at rest)."""
    return f"acct:{tenant_id}:{hashlib.sha256(sub.encode()).hexdigest()}"


def ip_key(ip: str | None) -> str | None:
    return f"ip:{ip}" if ip else None


def check(conn: Connection, keys: list[str | None]) -> None:
    """Raise 429 rate_limited (with Retry-After) when any key is locked."""
    keys = [k for k in keys if k]
    if not keys:
        return
    row = conn.execute(
        text(
            "SELECT max(locked_until) AS locked_until, now() AS db_now "
            "FROM ops.login_throttle "
            "WHERE key = ANY(:keys) AND locked_until > now()"
        ),
        {"keys": keys},
    ).mappings().first()
    if row is None or row["locked_until"] is None:
        return
    retry_after = max(
        1, math.ceil((row["locked_until"] - row["db_now"]).total_seconds())
    )
    raise rate_limited(
        "Too many failed login attempts. Try again later.",
        retry_after=retry_after,
    )


def register_failure(conn: Connection, keys: list[str | None]) -> None:
    """Count one failed login attempt for every non-null key."""
    settings = get_settings()
    for key in [k for k in keys if k]:
        conn.execute(
            _REGISTER_FAILURE_SQL,
            {
                "key": key,
                "window_secs": settings.login_window_seconds,
                "max_fails": settings.login_max_failures,
                "base_secs": settings.login_lockout_base_seconds,
                "max_secs": settings.login_lockout_max_seconds,
            },
        )


def register_success(conn: Connection, account_key_: str) -> None:
    """A successful login clears the account's failure counter and lock.

    The IP counter is intentionally NOT reset: a valid login must not launder
    an IP that is flooding the endpoint; the IP window decays on its own.
    """
    conn.execute(
        text(
            "UPDATE ops.login_throttle "
            "SET fail_count = 0, locked_until = NULL, updated_at = now() "
            "WHERE key = :key"
        ),
        {"key": account_key_},
    )


def register_hit(
    conn: Connection,
    key: str,
    *,
    window_secs: int,
    max_hits: int,
    base_secs: int = 30,
    max_secs: int = 3600,
) -> None:
    """Count one POSITIVE hit on `key` (spec 023, D-023-07).

    Same table and lockout arithmetic as ``register_failure``, but for
    endpoints where every request counts (not only failures): the public
    download page/bundle. After ``max_hits`` inside the sliding window the
    key locks out progressively (base * 2^(hits - max), capped) and
    ``check()`` raises 429 on subsequent requests. Counters live in
    PostgreSQL (ops plane, no RLS by design) so they survive restarts.
    """
    conn.execute(
        _REGISTER_FAILURE_SQL,
        {
            "key": key,
            "window_secs": window_secs,
            "max_fails": max_hits,
            "base_secs": base_secs,
            "max_secs": max_secs,
        },
    )
