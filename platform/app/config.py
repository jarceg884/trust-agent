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

    # --- Governance bundle signing (spec 019, D-GOH-02, ADR-016) -------------

    # RSA private key (PKCS#8 PEM, base64-encoded in the env var) that signs
    # the governance bundle JWS (RS256; HS256 is forbidden by constitution IV).
    # DECISION (documented in spec 019 §10): the key lives in the environment
    # like every other control-plane secret (TRUST_DEVICE_TOKEN_SECRET,
    # TRUST_VAULT_KEK_B64) — same fail-closed pattern, rotation = new env
    # value + new kid (the public JWKS at /.well-known/policy-jwks.json and
    # the pinned plugin key rotate with the profile distribution). Fail-closed
    # boot: missing/placeholder key refuses to start (a bundle without a real
    # signing key would be forgeable).
    policy_jws_private_key_b64: str = ""
    # `iss` claim of the bundle (§5.2: "pineado"). The plugin may verify it
    # via TRUST_AGENT_ISSUER; defaults to the canonical local control plane.
    policy_jws_issuer: str = "https://trust-agent.control-plane/"

    # --- Live policy & presence (spec 027) ----------------------------------
    # Heartbeat: the enrolled plugin reports presence on every sync cycle
    # (piggyback on TRUST_SYNC_INTERVAL, default 300 s). The response tells
    # the plugin the interval so it can self-adjust.
    heartbeat_interval_seconds: int = 300
    # A device is ONLINE while its last heartbeat is younger than this window
    # (default 2 intervals: tolerates one lost heartbeat). Floor 60 s.
    presence_online_window_seconds: int = 600
    # Audit sampling: at most one `device.heartbeat` audit row per device per
    # interval (D-027-05: no flooding of the immutable audit log).
    heartbeat_audit_interval_seconds: int = 3600

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
    # Spec 032: periodic model-catalog refresh window (hours) for tenants
    # with active llm connections (jobs-worker enqueues idempotently).
    catalog_refresh_hours: float = 6.0
    # Spec 018 §5 (D-018-07): live upstream MaaS endpoint + provider key —
    # the AUTHORITATIVE source for the SET of central models. The central
    # catalog refresh reconciles against GET {endpoint}/models: models the
    # upstream no longer serves go stale; new deployments are added with
    # NULL pricing (unknown until a doc-stamped migration) and registered
    # in the gateway. The key is used in-memory per call, never logged,
    # never in responses nor audit payloads. Empty => the refresh degrades
    # to the gateway-routing check only (never fail-open).
    maas_endpoint: str = ""
    maas_api_key: str = ""

    # --- Google identity brokering (spec 015, D-AUTH-02) ---------------------
    # Operator-provided Google OAuth credentials (Google Cloud Console).
    # Fail-closed: without them ensure_google_idp() adds NO identity provider
    # and org-config reports google_login_enabled=false. Redirect URI to
    # register per realm: {kc-base}/realms/<realm>/broker/google/endpoint.
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- Public organization self-signup (spec 026, mandato Miller) -----------
    # The Admin Console IS the SaaS entrance: new customers create their own
    # organization (born pending_activation, zero budget). The provider can
    # close the door with TRUST_SELF_SIGNUP_ENABLED=false (403 fail-closed).
    self_signup_enabled: bool = True

    # --- Provider operations (spec 004: tenant provisioning) ------------------
    # Bearer token for /api/admin/tenants* (platform_admin is NOT a tenant
    # role, so no user session can carry it — D-ONB-02). Empty = fail-closed.
    provider_token: str = ""
    # --- Provider super-user (spec 025, mandato Miller) ------------------------
    # The Provider Console operator is a REAL identity in the database
    # (ops.provider_operators), not an env secret. The bootstrap seeds the
    # initial super-user at startup: username from config, one-time password
    # generated and logged EXACTLY once (same OTA pattern as tenants,
    # D-ONB-05). TRUST_PROVIDER_OPERATOR_INITIAL_PASSWORD pins the initial
    # password instead of generating one (documented CI/test path; leave
    # EMPTY in production). First login forces a password change (D-PS-08).
    provider_operator_username: str = "provider-admin"
    provider_operator_initial_password: str = ""
    # Keycloak Admin REST (provider-side bootstrap, D-ONB-13). The admin
    # password MUST come from the environment; provisioning refuses to run
    # without it.
    keycloak_admin_url: str = "http://127.0.0.1:8080"
    keycloak_admin_user: str = "admin"
    keycloak_admin_password: str = ""
    # Redirect URIs registered on the per-tenant public client (comma-separated).
    # F1 (t_38ce1e82): both loopback names must be registered — the OIDC dance
    # derives redirect_uri from the Host header so a browser on
    # http://localhost:PORT stays on localhost (host-only cookies keep
    # applying) instead of being flipped to 127.0.0.1.
    # Spec 023 (D-023-13): the bundle's `trust-login` CLI helper runs a local
    # callback server on 127.0.0.1:8765 — its redirect URI must be registered
    # on the public client too (provisioning registers the list; the heal
    # entry ensure_web_client_redirect_uris merges it into existing realms).
    oidc_redirect_uris: str = (
        "http://127.0.0.1:3000/auth/callback,http://localhost:3000/auth/callback,"
        "http://127.0.0.1:8765/callback"
    )

    # --- Public Hermes download (spec 023, D-023-07) ---------------------------
    # Rate limits of the public per-tenant download endpoints. Positive
    # counters in ops.login_throttle (same family as org-config, D-DB-14):
    # they live in PostgreSQL, survive restarts and lock out progressively.
    # The page is cheap (30/min per IP); the bundle is the expensive resource
    # (5/min per IP+slug). Unknown-slug probing burns the login IP counter
    # (anti-enumeration, identical to GET /api/auth/org-config).
    download_page_rate_max: int = 30
    download_page_rate_window_seconds: int = 60
    download_bundle_rate_max: int = 5
    download_bundle_rate_window_seconds: int = 60
    # Source tree of the trust-agent-governance plugin that gets packaged into
    # the download bundle (spec 022 converged tree). In the API container the
    # Dockerfile copies it to /opt/trust-agent/hermes-plugins; on a bare-metal
    # dev run point it at the repo tree.
    hermes_plugin_dir: str = "/opt/trust-agent/hermes-plugins/trust-agent-governance"


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


def validate_policy_jws_key(settings: Settings) -> None:
    """Fail-closed boot check for the governance bundle signing key
    (spec 019, D-GOH-02, constitution IV).

    The bundle is the ONLY local authority the plugin trusts, so a missing
    or placeholder signing key refuses to boot exactly like the device
    token secret: a bundle signed with a known default would be forgeable
    and the whole L2 enforcement layer with it.
    """
    from .governance.jws_keys import load_signing_key

    load_signing_key(settings)  # raises RuntimeError on any problem
