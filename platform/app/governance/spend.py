"""Spend ingestion + budget enforcement (REQ-BUD-005…011, ADR-014, D-GOV-01…08).

Two phases per worker cycle:

1. **Ingest**: LiteLLM spend logs → `spend_ledger` (idempotent by
   request_id; only NEW rows move the counters) + `budget_counters` for
   the three levels in the same transaction. The MaaS margin is applied
   to `central` rows; `byok` rows count without margin (REQ-BUD-011).
2. **Enforce**: compare current-window counters against the active
   budgets; synchronize virtual keys with the gateway (block on cutoff,
   unblock on recovery/period reset — idempotent transitions only),
   emit audit events and inbox notifications (threshold deduped per
   user+scope+window).
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ..audit import service as audit
from ..db import tenant_tx
from ..models import access as model_access
from . import budgets, notifications as inbox
from .budgets import _budget_row, _spent_in_window, period_window

# Milli-centavo (D-GOV-17, spec 005 / migración 0029): ledger y counters en
# numeric(12,3) — centavos con 3 decimales. ROUND_HALF_UP en cada cuantización
# (conservador para el corte: nunca por debajo del costo real).
_MILLI_CENT = Decimal("0.001")


@dataclass
class RawSpend:
    """A spend-log row before tenant-scoped resolution."""

    request_id: str
    tenant_id: str
    user_id: str | None
    model_name: str
    cost_base_cents: Decimal | int  # milli-centavo (0.001¢); int legacy se coercen
    tokens_in: int
    tokens_out: int
    occurred_at: datetime


# --- Ingest ---------------------------------------------------------------------

def _as_decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _apply_margin(
    cost_base_cents: Decimal | int, mode: str, margin_bps: int,
) -> Decimal:
    """`cost_billed = cost_base × (10000 + bps)/10000` en Decimal exacto,
    re-cuantizado a 0.001¢ ROUND_HALF_UP (D-GOV-06 + D-GOV-17). BYOK sin
    margen (REQ-BUD-011)."""
    base = _as_decimal(cost_base_cents)
    if mode == "byok":
        return base
    billed = base * (10_000 + margin_bps) / Decimal(10_000)
    return billed.quantize(_MILLI_CENT, rounding=ROUND_HALF_UP)


def _bump_counters(
    conn: Connection, tenant_id: str, *, user_id: str | None,
    department_id: str | None, billed_cents: Decimal, when: datetime,
) -> None:
    """Add spent to the counters of the levels that govern the spend.

    The window of each level comes from that level's budget period
    (default monthly) — D-GOV-07.
    """
    for level, entity, dept, usr in (
        ("user", user_id, department_id, user_id),
        ("dept", department_id, department_id, None),
        ("org", tenant_id, None, None),
    ):
        if entity is None:
            continue
        budget = _budget_row(
            conn, level=level, department_id=dept, user_id=usr
        )
        period = budget["period"] if budget else "monthly"
        start, end = period_window(period, when.date())
        conn.execute(
            text(
                "INSERT INTO budget_counters (tenant_id, level, entity_id, "
                "period_start, period_end, spent_cents, budget_cents) "
                "VALUES (CAST(:t AS uuid), :level, CAST(:e AS uuid), :ps, :pe, "
                ":spent, :budget) "
                "ON CONFLICT (tenant_id, level, entity_id, period_start) "
                "DO UPDATE SET spent_cents = budget_counters.spent_cents "
                "+ EXCLUDED.spent_cents, updated_at = now()"
            ),
            {
                "t": tenant_id, "level": level, "e": str(entity),
                "ps": start, "pe": end, "spent": billed_cents,
                "budget": budget["amount_cents"] if budget else 0,
            },
        )


def ingest_entries(engine: Engine, tenant_id: str, entries: list[RawSpend]) -> dict:
    """Ledger + counters for one tenant (idempotent by request_id)."""
    ingested = 0
    skipped = 0
    with tenant_tx(engine, tenant_id) as conn:
        tenant_row = conn.execute(
            text("SELECT maas_margin_bps FROM tenants")
        ).mappings().first()
        if tenant_row is None:
            # RLS hides the tenants row: it no longer exists (QA H2,
            # t_82b2ffaa). Fail with a clear error instead of a cryptic
            # TypeError — run_once isolates the failure to this tenant.
            raise RuntimeError(
                f"spend ingest: tenant {tenant_id} is not visible under "
                "RLS (deleted while the cycle was running?)"
            )
        margin_bps = tenant_row["maas_margin_bps"]
        for e in entries:
            if not e.request_id:
                skipped += 1
                continue
            dept_row = (
                conn.execute(
                    text("SELECT department_id FROM users "
                         "WHERE id = CAST(:u AS uuid)"),
                    {"u": e.user_id},
                ).mappings().first()
                if e.user_id else None
            )
            if e.user_id and dept_row is None:
                skipped += 1  # user no longer exists in the tenant
                continue
            department_id = (
                str(dept_row["department_id"])
                if dept_row and dept_row["department_id"] else None
            )
            mode = model_access.resolve_mode(
                conn, tenant_id=tenant_id, model_name=e.model_name,
                user_id=e.user_id, department_id=department_id,
            )
            billed = _apply_margin(e.cost_base_cents, mode, margin_bps)
            result = conn.execute(
                text(
                    "INSERT INTO spend_ledger (tenant_id, request_id, user_id, "
                    "department_id, model_name, mode, cost_base_cents, "
                    "cost_billed_cents, tokens_in, tokens_out, occurred_at) "
                    "VALUES (CAST(:t AS uuid), :rid, CAST(:u AS uuid), "
                    "CAST(:d AS uuid), :model, :mode, :base, :billed, "
                    ":tin, :tout, :occurred) "
                    "ON CONFLICT (request_id) DO NOTHING"
                ),
                {
                    "t": tenant_id, "rid": e.request_id, "u": e.user_id,
                    "d": department_id, "model": e.model_name, "mode": mode,
                    "base": e.cost_base_cents, "billed": billed,
                    "tin": e.tokens_in, "tout": e.tokens_out,
                    "occurred": e.occurred_at,
                },
            )
            if result.rowcount:
                ingested += 1
                _bump_counters(
                    conn, tenant_id, user_id=e.user_id,
                    department_id=department_id, billed_cents=billed,
                    when=e.occurred_at,
                )
    return {"received": len(entries), "ingested": ingested, "skipped": skipped}


# --- Enforcement ------------------------------------------------------------------

def _load_budgets(conn: Connection) -> dict:
    rows = conn.execute(
        text("SELECT level, department_id, user_id, amount_cents, period, "
             "soft_threshold_pct FROM budgets WHERE status = 'active'")
    ).mappings()
    out = {"org": None, "dept": {}, "user": {}}
    for r in rows:
        if r["level"] == "org":
            out["org"] = dict(r)
        elif r["level"] == "dept":
            out["dept"][str(r["department_id"])] = dict(r)
        else:
            out["user"][str(r["user_id"])] = dict(r)
    return out


def _window_spent(conn: Connection, budget: dict, entity_id: str) -> Decimal | int:
    # `budgets.today()` (module attribute) is the clock seam: the rollover
    # test monkeypatches it (spec 013 D-013-01) — do NOT bind it by value.
    start, end = period_window(budget["period"], budgets.today())
    return _spent_in_window(
        conn, level=budget["level"], entity_id=entity_id,
        period_start=start, period_end=end,
    )


def _notify(
    conn: Connection, tenant_id: str, *, user_id: str, type_: str,
    payload: dict, dedupe: bool = False,
) -> bool:
    """Insert one inbox notification. Threshold notifications are deduped
    per (user, type, scope, window); cutoff notifications fire on every
    block transition (no dedupe)."""
    if dedupe and inbox.already_notified(
        conn, user_id=user_id, type=type_,
        scope=payload.get("scope", ""), period_start=payload.get("period_start", ""),
    ):
        return False
    inbox.notify(
        conn, tenant_id=tenant_id, user_id=user_id, type=type_, payload=payload
    )
    return True


def _dept_user_ids(conn: Connection, department_id: str) -> list[str]:
    rows = conn.execute(
        text("SELECT id FROM users WHERE department_id = CAST(:d AS uuid) "
             "AND status = 'active'"),
        {"d": department_id},
    ).mappings()
    return [str(r["id"]) for r in rows]


def enforce_tenant(engine: Engine, litellm, tenant_id: str) -> dict:
    """Synchronize key blocks + notifications with the current-window
    counters. Idempotent: only status transitions hit the gateway."""
    summary = {"blocked": 0, "unblocked": 0, "cutoff_events": 0,
               "threshold_notifications": 0, "budget_syncs": 0}
    with tenant_tx(engine, tenant_id) as conn:
        budgets = _load_budgets(conn)
        org = budgets["org"]
        org_spent = _window_spent(conn, org, tenant_id) if org else 0
        org_cutoff = bool(org and org_spent >= org["amount_cents"])

        dept_spent = {
            dept_id: _window_spent(conn, b, dept_id)
            for dept_id, b in budgets["dept"].items()
        }
        dept_cutoff = {
            dept_id: spent >= b["amount_cents"]
            for dept_id, (b, spent) in {
                d: (budgets["dept"][d], dept_spent[d]) for d in budgets["dept"]
            }.items()
        }
        user_spent = {
            uid: _window_spent(conn, b, uid)
            for uid, b in budgets["user"].items()
        }
        user_cutoff = {
            uid: spent >= b["amount_cents"]
            for uid, (b, spent) in {
                u: (budgets["user"][u], user_spent[u]) for u in budgets["user"]
            }.items()
        }

        keys = conn.execute(
            text(
                "SELECT k.id, k.user_id, k.litellm_key_hash, k.status, "
                "k.max_budget_cents, u.department_id FROM api_keys k "
                "JOIN users u ON u.id = k.user_id "
                "WHERE k.status IN ('active','blocked')"
            )
        ).mappings()

        for k in keys:
            uid = str(k["user_id"])
            dept = str(k["department_id"]) if k["department_id"] else None
            # Native max_budget sync (D-GOV-16, re-validación t_c4766c4d):
            # the gateway enforces the max_budget stamped at issuance; a
            # budget change at the platform must propagate to the key or
            # the recovery loop breaks (raise budget -> unblock -> the
            # gateway still refuses with the OLD native max_budget).
            user_budget = budgets["user"].get(uid)
            if (user_budget and k["max_budget_cents"] is not None
                    and k["max_budget_cents"] != user_budget["amount_cents"]):
                litellm.update_key_budget(
                    k["litellm_key_hash"],
                    user_budget["amount_cents"] / 100.0,
                )
                conn.execute(
                    text("UPDATE api_keys SET max_budget_cents = :amount, "
                         "updated_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"amount": user_budget["amount_cents"],
                     "id": str(k["id"])},
                )
                summary["budget_syncs"] += 1
            if org_cutoff:
                level, entity = "org", tenant_id
            elif dept and dept_cutoff.get(dept):
                level, entity = "dept", dept
            elif user_cutoff.get(uid):
                level, entity = "user", uid
            else:
                level, entity = None, None

            if level and k["status"] == "active":
                litellm.block_key(k["litellm_key_hash"])
                conn.execute(
                    text("UPDATE api_keys SET status = 'blocked', "
                         "updated_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": str(k["id"])},
                )
                audit.emit(
                    conn, tenant_id=tenant_id, actor_role="system",
                    action="budget.cutoff",
                    target_type="api_key", target_id=str(k["id"]),
                    payload={"level": level, "user_id": uid,
                             "department_id": dept},
                )
                audit.emit(
                    conn, tenant_id=tenant_id, actor_role="system",
                    action="model.key_blocked",
                    target_type="api_key", target_id=str(k["id"]),
                    payload={"reason": "budget_cutoff", "level": level},
                )
                _notify(
                    conn, tenant_id, user_id=uid, type_="budget_cutoff",
                    payload={"level": level, "scope": f"{level}:{entity}",
                             "department_id": dept},
                )
                if level in ("user", "dept") and dept:
                    for admin in _role_users(conn, "dept_admin", dept):
                        _notify(conn, tenant_id, user_id=admin,
                                type_="budget_cutoff",
                                payload={"level": level,
                                         "scope": f"{level}:{entity}",
                                         "department_id": dept,
                                         "affected_user_id": uid})
                if level == "org":
                    for admin in _role_users(conn, "org_admin", None):
                        _notify(conn, tenant_id, user_id=admin,
                                type_="budget_cutoff",
                                payload={"level": "org",
                                         "scope": f"org:{tenant_id}"})
                summary["blocked"] += 1
                summary["cutoff_events"] += 1
            elif not level and k["status"] == "blocked":
                litellm.unblock_key(k["litellm_key_hash"])
                conn.execute(
                    text("UPDATE api_keys SET status = 'active', "
                         "updated_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": str(k["id"])},
                )
                audit.emit(
                    conn, tenant_id=tenant_id, actor_role="system",
                    action="model.key_unblocked",
                    target_type="api_key", target_id=str(k["id"]),
                    payload={"reason": "budget_recovered_or_period_reset"},
                )
                summary["unblocked"] += 1

        summary["threshold_notifications"] = _thresholds(
            conn, tenant_id, budgets_by_level=budgets, org_spent=org_spent,
            dept_spent=dept_spent, user_spent=user_spent,
            org_cutoff=org_cutoff, dept_cutoff=dept_cutoff,
            user_cutoff=user_cutoff,
        )
    return summary


def _role_users(conn: Connection, role_key: str, department_id: str | None) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT user_id FROM user_roles WHERE role_key = :role "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid))"
        ),
        {"role": role_key, "dept": department_id},
    ).mappings()
    return [str(r["user_id"]) for r in rows]


def _thresholds(
    conn: Connection, tenant_id: str, *, budgets_by_level: dict,
    org_spent: Decimal | int, dept_spent: dict, user_spent: dict,
    org_cutoff: bool, dept_cutoff: dict, user_cutoff: dict,
) -> int:
    """80 % soft-threshold notifications (REQ-BUD-009), deduped per window.

    The parameter is named `budgets_by_level` (NOT `budgets`): the module
    `budgets` must stay reachable for the `budgets.today()` clock seam
    (spec 013 D-013-01) — shadowing it crashed every threshold check with
    AttributeError 'dict' object has no attribute 'today'.
    """
    sent = 0

    def check(budget: dict, spent: Decimal | int, level: str, entity: str,
              recipients: list[str], extra: dict | None = None) -> None:
        nonlocal sent
        if not budget or spent <= 0:
            return
        # Umbral en Decimal exacto (D-GOV-17): la comparación contra el
        # acumulado milli-centavo no introduce redondeo adicional.
        threshold = (
            Decimal(budget["amount_cents"])
            * budget["soft_threshold_pct"] / Decimal(100)
        )
        if spent < threshold:
            return
        start, _end = period_window(budget["period"], budgets.today())
        payload = {
            "level": level, "scope": f"{level}:{entity}",
            "period_start": start.isoformat(),
            "budget_cents": budget["amount_cents"],
            "spent_cents": float(spent),  # json.dumps no serializa Decimal
            **(extra or {}),
        }
        any_new = False
        for uid in recipients:
            if _notify(conn, tenant_id, user_id=uid,
                       type_="budget_threshold_80", payload=payload,
                       dedupe=True):
                any_new = True
                sent += 1
        if any_new:
            audit.emit(
                conn, tenant_id=tenant_id, actor_role="system",
                action="budget.threshold", target_type="budget",
                target_id=level,
                payload={"level": level, "scope": f"{level}:{entity}",
                         "period_start": start.isoformat(),
                         "spent_cents": float(spent),
                         "budget_cents": budget["amount_cents"]},
            )

    if budgets_by_level["org"] and not org_cutoff:
        check(budgets_by_level["org"], org_spent, "org", tenant_id,
              _role_users(conn, "org_admin", None))
    for dept_id, b in budgets_by_level["dept"].items():
        if not dept_cutoff.get(dept_id):
            check(b, dept_spent[dept_id], "dept", dept_id,
                  _dept_user_ids(conn, dept_id)
                  + _role_users(conn, "dept_admin", dept_id),
                  extra={"department_id": dept_id})
    for uid, b in budgets_by_level["user"].items():
        if not user_cutoff.get(uid):
            dept = None
            row = conn.execute(
                text("SELECT department_id FROM users WHERE id = CAST(:u AS uuid)"),
                {"u": uid},
            ).mappings().first()
            if row and row["department_id"]:
                dept = str(row["department_id"])
            recipients = [uid]
            if dept:
                recipients += _role_users(conn, "dept_admin", dept)
            check(b, user_spent[uid], "user", uid, recipients,
                  extra={"department_id": dept})
    return sent
