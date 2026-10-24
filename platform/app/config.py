"""Application settings (environment-driven, fail-closed).

All configuration comes from the environment (constitution VII: local-first,
no secrets in the repo). Missing critical settings refuse to boot.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRUST_", extra="ignore")

    # --- Database -----------------------------------------------------------
    # The app ALWAYS connects as trust_app (no BYPASSRLS, subject to RLS).
    database_url: str  # required: fail-closed if missing
    # Dedicated claim connection for the job worker (cross-tenant queue claim,
    # D-CP-10). Optional: without it the worker cannot claim jobs.
    database_migrator_url: str | None = None
    # Defense-in-depth lock timeout (QA H3, t_82b2ffaa): every pooled app
    # connection is created with this `lock_timeout` (libpq options in
    # build_engine). The audit_log chain trigger takes
    # pg_advisory_xact_lock per tenant; with H1 fixed there is no known
    # deadlock, but a stuck audit writer must never block a tenant's audit
    # trail FOREVER. Failure behaviour: the blocked statement aborts with
    # SQLSTATE 55P03 (lock_not_available) after this timeout, its
    # transaction rolls back and the request fails loudly (503) — the hash
    # chain stays consistent because the INSERT never happened. The
    # migrator engine is NOT bounded (job claims use SKIP LOCKED and never
    # wait on the advisory lock).
    database_lock_timeout: str = "10s"

    # --- Identity (OIDC / Keycloak) ------------------------------------------
    # Comma-separated list of accepted issuer base URLs (local Keycloak +
    # test IdP). Issuers must match {base}/realms/{realm}.
    idp_base_urls: str = "http://127.0.0.1:8080"
    oidc_audience: str = "trust-agent-web"
    jwt_leeway_seconds: int = 60

    # --- MFA evidence (D-MFA-01, spec 003/007) --------------------------------
    # Minimum numeric `acr` accepted as MFA evidence. Keycloak 26 never emits
    # `amr`; with the realm's `acr.loa.map` = {"1":0,"2":1} and the LoA-1
    # condition on the Conditional OTP flow, password+OTP logins carry
    # acr "2" and password-only logins carry "1". See app/auth/mfa.py.
    mfa_min_acr: int = 2

    # --- Sessions ------------------------------------------------------------
    session_ttl_minutes: int = 480
    cookie_name: str = "trust_session"
    cookie_secure: bool = True

    # --- Login rate limiting (REQ-SEC-006, ops.login_throttle / D-DB-14) -----
    login_max_failures: int = 5            # failures before the first lockout
    login_window_seconds: int = 900        # sliding failure window (15 min)
    login_lockout_base_seconds: int = 30   # first lock; doubles per extra failure
    login_lockout_max_seconds: int = 86400  # lock cap (24 h)

    # --- Device tokens (issued by the control plane itself, ADR-013) ---------
    # HS256 signing key. Fail-closed like every other secret: empty or a
    # leftover "change-me" placeholder refuses to boot (validated in
    # create_app() via validate_device_token_secret — a known default would
    # let anyone forge device tokens).
    device_token_secret: str = ""
    device_token_ttl_minutes: int = 15

    # --- Vault KEK (envelope encryption master key, ADR-009) -----------------
    # 32 bytes, base64-encoded. NEVER stored in the database.
    vault_kek_b64: str = ""
    vault_key_id: str = "kek-v1"

    # --- LiteLLM proxy (model control plane, spec 005 / D-GOV-15) ------------
    # Admin API base URL (e.g. http://127.0.0.1:4000). Empty => key issuance,
    # spend ingestion and enforcement refuse with 503 (fail-closed, never
    # fail-open — budgets without a gateway cannot be governed).
    litellm_base_url: str = ""
    # LITELLM_MASTER_KEY: required for /key/* and /spend/* admin routes.
    litellm_master_key: str = ""

    # --- Provider operations (spec 004: tenant provisioning) ------------------
    # Bearer token for /api/admin/tenants* (platform_admin is NOT a tenant
    # role, so no user session can carry it — D-ONB-02). Empty = fail-closed.
    provider_token: str = ""
    # Keycloak Admin REST (provider-side bootstrap, D-ONB-13). The admin
    # password MUST come from the environment; provisioning refuses to run
    # without it.
    keycloak_admin_url: str = "http://127.0.0.1:8080"
    keycloak_admin_user: str = "admin"
    keycloak_admin_password: str = ""
    # Redirect URIs registered on the per-tenant public client (comma-separated).
    oidc_redirect_uris: str = (
        "http://127.0.0.1:3000/auth/callback,http://127.0.0.1:9090/auth/callback"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_device_token_secret(settings: Settings) -> None:
    """Fail-closed boot check (constitution IV, QA t_993c71d5).

    The control plane must not start with a missing or placeholder
    device-token signing key: a known default would let anyone forge
    device tokens. Defense in depth on top of the DeviceGate (valid
    session + tenant/user claim match + active device row).
    """
    secret = settings.device_token_secret
    if not secret or "change-me" in secret.lower():
        raise RuntimeError(
            "TRUST_DEVICE_TOKEN_SECRET is missing or still a placeholder "
            "('change-me'). Set a strong random value (e.g. "
            "`openssl rand -base64 32`) before starting the control plane "
            "— device token signing is fail-closed (constitution IV)."
        )
