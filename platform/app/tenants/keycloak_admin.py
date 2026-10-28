"""Keycloak Admin REST client for tenant provisioning (spec 004, D-ONB-12/13).

Provider-side only. The ONLY password grant in the system is the admin-cli
bootstrap against the master realm (ADR-015, documented exception — never
offered to end users). Every step is idempotent (ensure_*) so a retry after a
crash converges, and the whole realm is compensable via delete_realm().

Keycloak 26 pitfall (verified in spec 003): the declarative User Profile
silently DROPS undeclared attributes, so every provisioned realm MUST set
unmanagedAttributePolicy=ENABLED or the tenant_id attribute (and therefore
the signed claim) never reaches the ID token.
"""
import secrets
from typing import Any

import httpx

from ..config import Settings

WEB_CLIENT_ID = "trust-agent-web"
TENANT_MAPPER_NAME = "tenant_id"

# --- MFA via ACR/LoA (D-MFA-01) ---------------------------------------------
# Keycloak 26 never emits `amr`; the control-plane MFA gate therefore reads
# `acr`. With this realm config a password+OTP login carries acr:"2" and a
# password-only login carries acr:"1" (see app/auth/mfa.py).
# PITFALL (verified live): the realm attribute is "acr.loa.map"
# (Constants.ACR_LOA_MAP) — NOT "acr-to-loa-map" as the admin console label
# suggests; a wrong key is silently ignored and requested acr values fall
# back to raw numeric interpretation.
ACR_LOA_MAP_ATTR = "acr.loa.map"
# Spec 015 (D-AUTH-06, probe S1..S8): acr "1" → LoA 1 (password-only),
# acr "2" → LoA 2 (password + OTP). The OLD map {"1":0,"2":1} made the
# Conditional OTP sub-flow the ONLY LoA-raiser, which forced the OTP form on
# every fresh login regardless of the requested acr (ConditionalLoaAuthenticator
# returns true unconditionally while currentLoa < 1 — verified against the
# KC 26.2.5 source, skill identity/keycloak26-auth-ux §1).
ACR_LOA_MAP_JSON = '{"1":1,"2":2}'
# The built-in "browser" flow cannot be modified ("illegal to add execution
# to a built in flow"), so a copy is created and bound as the realm's
# browserFlow.
BROWSER_LOA_FLOW = "trust-browser-loa"
LOA_CONDITION_PROVIDER = "conditional-level-of-authentication"
LOA_CONDITION_MAX_AGE = "36000"
# New wiring (probe-verified): the username+password form lives INSIDE a
# conditional "Password LoA" sub-flow (LoA level 1) so a fresh login raises
# the session LoA to 1 without touching OTP; "Conditional OTP" keeps ONLY a
# LoA condition (level 2). CRITICAL: within `forms` the conditional sub-flows
# run by PRIORITY — "Password LoA" must run BEFORE "Conditional OTP" (the OTP
# form evaluates to 400 UNKNOWN_USER when it runs with authUser == null).
PASSWORD_LOA_SUBFLOW = "Password LoA"
PASSWORD_LOA_PRIORITY = 15      # < Conditional OTP priority (20)
# The copied "… Conditional OTP" sub-flow must keep ONLY the LoA condition:
# with BOTH "Condition - user configured" and the LoA condition present,
# Keycloak raises the session LoA when the LoA condition merely MATCHES —
# even if the flow is then SKIPPED for users without OTP (verified live:
# password-only logins got acr:"2"). With the LoA condition alone the only
# path to LoA 2 is the OTP form, and for users without a TOTP credential
# OTPFormAuthenticator.setRequiredActions() forces inline CONFIGURE_TOTP
# enrollment — so nobody is locked out and nobody gets acr:"2" without a
# second factor.
USER_CONFIGURED_PROVIDER = "conditional-user-configured"
USERNAME_PASSWORD_PROVIDER = "auth-username-password-form"

# --- Direct Access Grant (spec 015 v1.3.0, ADR-019) ---------------------------
# The auth panel (Keycloak never visible) logs users in via ROPC against the
# web client, so `directAccessGrantsEnabled` must be true on `trust-agent-web`
# (amends ADR-015; product decision Miller 2026-10-28).
#
# MFA evidence in the direct grant (D-AUTH-13, probe-verified against KC
# 26.2.5 live): the STOCK direct grant flow emits acr:"1" even when the OTP
# validated (no LoA conditions). Wiring: a copy of the built-in flow with ONE
# "Condition - LoA level 2" ADDED to its "… Conditional OTP" sub-flow (the
# stock "Condition - user configured" is KEPT). In a direct grant
# requestedLoa is always 0 and currentLoa starts at 0, so the LoA condition
# evaluates TRUE unconditionally (ConditionalLoaAuthenticator returns true
# while currentLoa < MINIMUM_LOA) and the user-configured condition alone
# decides whether the sub-flow is skipped. The sub-flow therefore only
# SUCCEEDS when the OTP actually validated → LoA 2 → the tokens carry the
# signed `acr:"2"` that mfa_evidence() (D-MFA-01) already accepts.
#
# FAIL-OPEN CHECK (live-verified): a user WITHOUT an OTP credential gets
# acr:"1" — the browser-flow bug "LoA granted when the sub-flow is skipped"
# (see _restructure_loa_flow) does NOT reproduce in the direct grant flow.
DIRECT_GRANT_FLOW = "trust-direct-grant-loa"
DIRECT_GRANT_OTP_SUBFLOW_MARKER = "Conditional OTP"
DIRECT_GRANT_OTP_LOA_LEVEL = "2"

# --- White-label login theme (spec 015, D-AUTH-01) ---------------------------
# KC 26 default theme is keycloak.v2; our theme extends it (PatternFly v5)
# and is deployed via the idp-themes/ volume → /opt/keycloak/themes.
LOGIN_THEME = "trust-agent"
# Per-tenant message overrides (t_e68687ee): locale of the realm localization
# texts managed by ensure_login_theme. "en" matches the app UI language and
# the theme's default locale; realms ship with internationalization disabled
# and the overrides still apply (verified live against KC 26.2.5).
LOCALIZATION_LOCALE = "en"
# TOTP enrollment page h1 — the one login surface where the tenant product
# name reads naturally ("Secure your <product> account"). The generic
# fallback ("Two-factor authentication") lives in the theme's
# login/messages/messages_en.properties.
TOTP_TITLE_KEY = "loginTotpTitle"

# --- Google identity brokering (spec 015, D-AUTH-02) -------------------------
GOOGLE_IDP_ALIAS = "google"

# --- tenant_id for brokered users (spec 015, D-AUTH-03) ----------------------
# Users auto-created by "Create User If Unique" carry NO user attributes, so
# the attribute mapper never emits tenant_id for them. A hardcode-claim mapper
# on the web client (constant per realm — one realm per tenant) guarantees
# every token of the realm carries the signed claim.
HARDCODE_MAPPER_NAME = "tenant_id_hardcode"


class KeycloakAdminError(Exception):
    """Admin REST failure carrying the provisioning step that failed."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


class KeycloakAdminClient:
    def __init__(self, settings: Settings | None = None) -> None:
        from ..config import get_settings

        self.settings = settings or get_settings()
        self._token: str | None = None

    # -- low-level helpers ---------------------------------------------------

    @property
    def base_url(self) -> str:
        return self.settings.keycloak_admin_url.rstrip("/")

    def issuer_for(self, realm: str) -> str:
        return f"{self.base_url}/realms/{realm}"

    def _admin_token(self) -> str:
        """Internal bootstrap ONLY: admin-cli + password against master
        (ADR-015). Fail-closed without the configured admin password."""
        if not self.settings.keycloak_admin_password:
            raise KeycloakAdminError(
                "keycloak.bootstrap",
                "TRUST_KEYCLOAK_ADMIN_PASSWORD is not configured; "
                "provisioning refuses to run (fail-closed).",
            )
        resp = httpx.post(
            f"{self.base_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.settings.keycloak_admin_user,
                "password": self.settings.keycloak_admin_password,
            },
                timeout=15,
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.bootstrap", f"admin token: HTTP {resp.status_code}"
            )
        return resp.json()["access_token"]

    def _auth(self) -> dict[str, str]:
        if self._token is None:
            self._token = self._admin_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _request(
        self, method: str, path: str, *, step: str,
        json_body: Any | None = None, retry_auth: bool = True,
    ) -> httpx.Response:
        resp = httpx.request(
            method, f"{self.base_url}{path}",
            json=json_body, headers=self._auth(), timeout=20,
        )
        if resp.status_code == 401 and retry_auth:
            # Token expired mid-provisioning: re-bootstrap once.
            self._token = None
            return self._request(
                method, path, step=step, json_body=json_body, retry_auth=False
            )
        if resp.status_code >= 500:
            raise KeycloakAdminError(
                step, f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}"
            )
        return resp

    # -- provisioning steps (idempotent) --------------------------------------

    def realm_exists(self, realm: str) -> bool:
        resp = self._request("GET", f"/admin/realms/{realm}", step="keycloak.realm")
        return resp.status_code == 200

    def ensure_realm(self, realm: str) -> None:
        resp = self._request("GET", f"/admin/realms/{realm}", step="keycloak.realm")
        if resp.status_code == 200:
            return
        resp = self._request(
            "POST", "/admin/realms", step="keycloak.realm",
            json_body={"realm": realm, "enabled": True},
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                "keycloak.realm", f"create realm: HTTP {resp.status_code} {resp.text[:200]}"
            )

    def enable_unmanaged_attributes(self, realm: str) -> None:
        """Keycloak 26: without ENABLED the user attribute tenant_id is
        silently dropped and the mapper never emits the claim (D-ONB-12)."""
        resp = self._request(
            "GET", f"/admin/realms/{realm}/users/profile", step="keycloak.user_profile"
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.user_profile", f"GET profile: HTTP {resp.status_code}"
            )
        profile = resp.json()
        if profile.get("unmanagedAttributePolicy") == "ENABLED":
            return
        profile["unmanagedAttributePolicy"] = "ENABLED"
        resp = self._request(
            "PUT", f"/admin/realms/{realm}/users/profile", step="keycloak.user_profile",
            json_body=profile,
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.user_profile",
                f"PUT profile: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def _web_client_uuid(self, realm: str) -> str:
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/clients?clientId={WEB_CLIENT_ID}",
            step="keycloak.client",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.client", f"lookup client: HTTP {resp.status_code}"
            )
        clients = resp.json()
        if clients:
            client_uuid = clients[0]["id"]
            self._ensure_client_redirect_uris(realm, client_uuid)
            return client_uuid
        redirect_uris = [
            u.strip() for u in self.settings.oidc_redirect_uris.split(",") if u.strip()
        ]
        resp = self._request(
            "POST", f"/admin/realms/{realm}/clients", step="keycloak.client",
            json_body={
                "clientId": WEB_CLIENT_ID,
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": True,
                "redirectUris": redirect_uris,
                "attributes": {"pkce.code.challenge.method": "S256"},
            },
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                "keycloak.client",
                f"create client: HTTP {resp.status_code} {resp.text[:200]}",
            )
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/clients?clientId={WEB_CLIENT_ID}",
            step="keycloak.client",
        )
        clients = resp.json()
        if not clients:
            raise KeycloakAdminError("keycloak.client", "client missing after create")
        return clients[0]["id"]

    def ensure_web_client_redirect_uris(self, realm: str) -> None:
        """F1 (t_38ce1e82): public heal entry for the web client redirect URIs.

        Delegates to `_web_client_uuid`, which on lookup merges the configured
        redirect URIs into pre-existing clients. Used by the idempotent
        re-provision path so re-running provisioning repairs realms created
        before both loopback names (127.0.0.1 + localhost) were registered.
        """
        self._web_client_uuid(realm)

    def ensure_direct_grant_enabled(self, realm: str) -> None:
        """ADR-019: heal `directAccessGrantsEnabled` on the web client.

        Realms provisioned before the auth panel keep the ADR-015 value
        (false); this flips it idempotently (only PUTs when it differs).
        """
        client_uuid = self._web_client_uuid(realm)
        resp = self._request(
            "GET", f"/admin/realms/{realm}/clients/{client_uuid}",
            step="keycloak.client",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.client", f"get client: HTTP {resp.status_code}"
            )
        client = resp.json()
        if client.get("directAccessGrantsEnabled"):
            return
        client["directAccessGrantsEnabled"] = True
        resp = self._request(
            "PUT", f"/admin/realms/{realm}/clients/{client_uuid}",
            step="keycloak.client", json_body=client,
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.client",
                f"enable directAccessGrants: HTTP {resp.status_code} "
                f"{resp.text[:200]}",
            )

    def ensure_direct_grant_flow(self, realm: str) -> None:
        """D-AUTH-13 (spec 015 v1.3.0): wire `trust-direct-grant-loa`.

        Copy of the built-in `direct grant` flow with a "Condition - LoA
        level 2" added to its "… Conditional OTP" sub-flow (user-configured
        kept), bound as the realm's directGrantFlow. Idempotent: converges
        when the copy exists, the condition is configured at level 2 and
        the realm binding points at the copy.
        """
        rep = self._get_realm_rep(realm, "keycloak.direct_grant_flow")
        if rep.get("directGrantFlow") == DIRECT_GRANT_FLOW and \
                self._direct_grant_flow_ready(realm):
            return
        resp = self._request(
            "POST",
            f"/admin/realms/{realm}/authentication/flows/direct%20grant/copy",
            step="keycloak.direct_grant_flow",
            json_body={"newName": DIRECT_GRANT_FLOW},
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                "keycloak.direct_grant_flow",
                f"copy direct grant flow: HTTP {resp.status_code} "
                f"{resp.text[:200]}",
            )
        self._wire_direct_grant_loa(realm)
        resp = self._request(
            "PUT", f"/admin/realms/{realm}", step="keycloak.direct_grant_flow",
            json_body={"directGrantFlow": DIRECT_GRANT_FLOW},
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.direct_grant_flow",
                f"bind directGrantFlow: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def _wire_direct_grant_loa(self, realm: str) -> None:
        """Bring the copied flow to the D-AUTH-13 shape: the Conditional OTP
        sub-flow carries ONE LoA condition at level 2 (user-configured is
        kept from the stock copy). Sub-flows are addressable by their
        (display) alias — the listing is nested-inclusive, so children are
        listed via the sub-flow alias, not the parent (live-verified)."""
        flat = self._flow_executions(realm, DIRECT_GRANT_FLOW,
                                     "keycloak.direct_grant_flow")
        otp = next(
            (e for e in flat
             if DIRECT_GRANT_OTP_SUBFLOW_MARKER in (e.get("displayName") or "")),
            None,
        )
        if otp is None:
            raise KeycloakAdminError(
                "keycloak.direct_grant_flow",
                f"flow '{DIRECT_GRANT_FLOW}' is missing its Conditional OTP "
                "sub-flow",
            )
        otp_alias = otp["displayName"]
        kids = self._flow_executions(realm, otp_alias,
                                     "keycloak.direct_grant_flow")
        loa = next(
            (e for e in kids
             if e.get("providerId") == LOA_CONDITION_PROVIDER), None,
        )
        if loa is None:
            self._add_execution(realm, otp["flowId"], LOA_CONDITION_PROVIDER,
                                "REQUIRED", 20)
            kids = self._flow_executions(realm, otp_alias,
                                         "keycloak.direct_grant_flow")
            loa = next(
                (e for e in kids
                 if e.get("providerId") == LOA_CONDITION_PROVIDER), None,
            )
            if loa is None:
                raise KeycloakAdminError(
                    "keycloak.direct_grant_flow",
                    "LoA condition missing after create",
                )
        if self._loa_condition_level(realm, loa) != DIRECT_GRANT_OTP_LOA_LEVEL:
            self._set_loa_config(
                realm, loa["id"], DIRECT_GRANT_OTP_LOA_LEVEL,
                alias="trust-dg-loa-otp-2",
            )

    def _direct_grant_flow_ready(self, realm: str) -> bool:
        """True when the bound direct grant flow has the D-AUTH-13 shape."""
        try:
            flat = self._flow_executions(realm, DIRECT_GRANT_FLOW,
                                         "keycloak.direct_grant_flow")
        except KeycloakAdminError:
            return False
        otp = next(
            (e for e in flat
             if DIRECT_GRANT_OTP_SUBFLOW_MARKER in (e.get("displayName") or "")),
            None,
        )
        if otp is None:
            return False
        state = self._subflow_loa_state(realm, otp["displayName"])
        return state["loa_level"] == DIRECT_GRANT_OTP_LOA_LEVEL

    def direct_grant_flow_state(self, realm: str) -> dict:
        """Retrofit reporting: binding + wiring + client flag (never raises)."""
        state = {"bound_flow": None, "loa_level": None,
                 "has_user_configured": None, "client_direct_grants": None}
        try:
            rep = self._get_realm_rep(realm, "keycloak.direct_grant_flow")
            state["bound_flow"] = rep.get("directGrantFlow")
            if rep.get("directGrantFlow") == DIRECT_GRANT_FLOW:
                sub = self._subflow_loa_state_for(realm)
                state["loa_level"] = sub.get("loa_level")
                state["has_user_configured"] = sub.get("has_user_configured")
            client_uuid = self._web_client_uuid(realm)
            resp = self._request(
                "GET", f"/admin/realms/{realm}/clients/{client_uuid}",
                step="keycloak.client",
            )
            if resp.status_code == 200:
                state["client_direct_grants"] = \
                    resp.json().get("directAccessGrantsEnabled")
        except KeycloakAdminError:
            pass
        return state

    def _subflow_loa_state_for(self, realm: str) -> dict:
        flat = self._flow_executions(realm, DIRECT_GRANT_FLOW,
                                     "keycloak.direct_grant_flow")
        otp = next(
            (e for e in flat
             if DIRECT_GRANT_OTP_SUBFLOW_MARKER in (e.get("displayName") or "")),
            None,
        )
        if otp is None:
            return {}
        return self._subflow_loa_state(realm, otp["displayName"])

    def _ensure_client_redirect_uris(self, realm: str, client_uuid: str) -> None:
        """F1 (t_38ce1e82): heal redirect URIs of pre-existing clients.

        Realms provisioned before both loopback names (127.0.0.1 + localhost)
        were registered only carry the 127.0.0.1 callbacks, which forces the
        OIDC dance back to 127.0.0.1 and drops the host-only cookies of a
        browser on localhost. Idempotent: only PUTs when a configured URI is
        missing.
        """
        resp = self._request(
            "GET", f"/admin/realms/{realm}/clients/{client_uuid}",
            step="keycloak.client",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.client", f"get client: HTTP {resp.status_code}"
            )
        client = resp.json()
        current = set(client.get("redirectUris") or [])
        wanted = [
            u.strip() for u in self.settings.oidc_redirect_uris.split(",") if u.strip()
        ]
        missing = [u for u in wanted if u not in current]
        if not missing:
            return
        client["redirectUris"] = sorted(current | set(wanted))
        resp = self._request(
            "PUT", f"/admin/realms/{realm}/clients/{client_uuid}",
            step="keycloak.client", json_body=client,
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.client",
                f"update redirect uris: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def ensure_tenant_id_mapper(self, realm: str) -> None:
        """user attribute tenant_id -> signed id_token claim (ADR-015)."""
        client_uuid = self._web_client_uuid(realm)
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models",
            step="keycloak.mapper",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.mapper", f"list mappers: HTTP {resp.status_code}"
            )
        if any(m.get("name") == TENANT_MAPPER_NAME for m in resp.json()):
            return
        resp = self._request(
            "POST",
            f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models",
            step="keycloak.mapper",
            json_body={
                "name": TENANT_MAPPER_NAME,
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-attribute-mapper",
                "config": {
                    "user.attribute": "tenant_id",
                    "claim.name": "tenant_id",
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                    "access.token.claim": "true",
                    "userinfo.token.claim": "true",
                },
            },
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                "keycloak.mapper",
                f"create mapper: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def find_user(self, realm: str, username: str) -> dict | None:
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/users?username={username}&exact=true",
            step="keycloak.admin_user",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.admin_user", f"lookup user: HTTP {resp.status_code}"
            )
        users = resp.json()
        return users[0] if users else None

    def find_user_by_email(self, realm: str, email: str) -> dict | None:
        """Auth panel (ADR-019): exact email lookup in the realm."""
        from urllib.parse import quote

        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/users?email={quote(email)}&exact=true",
            step="keycloak.admin_user",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.admin_user", f"lookup user by email: HTTP {resp.status_code}"
            )
        users = resp.json()
        return users[0] if users else None

    def user_has_otp_credential(self, realm: str, user_id: str) -> bool:
        """True when the user has an OTP (TOTP) credential — drives the
        `mfa_code_required` hint of the panel (D-AUTH-16: only shown for
        accounts that actually have MFA configured)."""
        resp = self._request(
            "GET", f"/admin/realms/{realm}/users/{user_id}/credentials",
            step="keycloak.admin_user",
        )
        if resp.status_code != 200:
            return False  # fail-closed: no hint without a confirmed lookup
        return any(c.get("type") == "otp" for c in resp.json())

    def set_user_password(
        self, realm: str, user_id: str, password: str, *,
        clear_action: str | None = "UPDATE_PASSWORD",
    ) -> None:
        """Auth panel (D-AUTH-15): set a PERMANENT password via Admin REST
        and clear the UPDATE_PASSWORD required action. The caller MUST have
        proven the CURRENT password first (direct grant 200, or the 400
        "Account is not fully set up" that only occurs after credential
        validation — live-verified). The password transits once towards
        Admin REST and is never stored (D-AUTH-04 precedent)."""
        resp = self._request(
            "PUT",
            f"/admin/realms/{realm}/users/{user_id}/reset-password",
            step="keycloak.panel_password",
            json_body={"type": "password", "value": password,
                       "temporary": False},
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.panel_password",
                f"reset password: HTTP {resp.status_code} {resp.text[:200]}",
            )
        if clear_action is None:
            return
        user = self.get_user(realm, user_id) or {}
        actions = [a for a in (user.get("requiredActions") or [])
                   if a != clear_action]
        resp = self._request(
            "PUT", f"/admin/realms/{realm}/users/{user_id}",
            step="keycloak.panel_password",
            json_body={"requiredActions": actions},
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.panel_password",
                f"clear required action: HTTP {resp.status_code} "
                f"{resp.text[:200]}",
            )

    def ensure_admin_user(
        self, realm: str, *, username: str, email: str, tenant_id: str,
        display_name: str, require_totp_action: bool = False,
    ) -> tuple[str, str]:
        """Create (or re-assert) the initial org_admin user.

        Returns (user_uuid, one_time_password). The password is TEMPORARY:
        Keycloak forces a change on first login (UPDATE_PASSWORD).

        Spec 015 (D-AUTH-06): CONFIGURE_TOTP is NO LONGER forced as a
        required action — TOTP enrollment happens inline through the OTP form
        when the app requests acr_values=2 (probe S2/S3: the LoA-2 condition
        triggers OTPFormAuthenticator.setRequiredActions → inline
        CONFIGURE_TOTP). ``require_totp_action=True`` restores the legacy
        behavior for callers that explicitly want forced enrollment.

        The one-time password is generated with secrets and returned exactly
        once — never stored in our DB.
        """
        payload = {
            "username": username,
            "enabled": True,
            "email": email,
            "firstName": (display_name or "Admin").split(" ")[0],
            "lastName": " ".join((display_name or "Admin").split(" ")[1:]) or "Admin",
            "emailVerified": True,
            "requiredActions": ["CONFIGURE_TOTP"] if require_totp_action else [],
            "attributes": {"tenant_id": [tenant_id]},
        }
        existing = self.find_user(realm, username)
        if existing is None:
            resp = self._request(
                "POST", f"/admin/realms/{realm}/users", step="keycloak.admin_user",
                json_body=payload,
            )
            if resp.status_code not in (201, 409):
                raise KeycloakAdminError(
                    "keycloak.admin_user",
                    f"create user: HTTP {resp.status_code} {resp.text[:200]}",
                )
            existing = self.find_user(realm, username)
            if existing is None:
                raise KeycloakAdminError(
                    "keycloak.admin_user", "user missing after create"
                )
        else:
            # Re-assert attributes + required actions (idempotent retry).
            resp = self._request(
                "PUT", f"/admin/realms/{realm}/users/{existing['id']}",
                step="keycloak.admin_user", json_body=payload,
            )
            if resp.status_code not in (204, 409):
                raise KeycloakAdminError(
                    "keycloak.admin_user",
                    f"re-assert user: HTTP {resp.status_code} {resp.text[:200]}",
                )

        one_time_password = secrets.token_urlsafe(18)
        resp = self._request(
            "PUT",
            f"/admin/realms/{realm}/users/{existing['id']}/reset-password",
            step="keycloak.admin_password",
            json_body={"type": "password", "value": one_time_password,
                       "temporary": True},
        )
        if resp.status_code not in (204, 409):
            raise KeycloakAdminError(
                "keycloak.admin_password",
                f"reset password: HTTP {resp.status_code} {resp.text[:200]}",
            )
        return existing["id"], one_time_password

    def ensure_user_with_one_time_password(
        self, realm: str, *, username: str, email: str, tenant_id: str,
        display_name: str,
    ) -> tuple[str, str]:
        """Invite path (spec 012): same contract as ensure_admin_user —
        create/re-assert the user with a one-time temporary password and
        CONFIGURE_TOTP. Alias kept separate so provisioning (spec 004)
        and invites (spec 012) can evolve independently."""
        return self.ensure_admin_user(
            realm, username=username, email=email, tenant_id=tenant_id,
            display_name=display_name,
        )

    def create_user_with_password(
        self, realm: str, *, username: str, email: str, tenant_id: str,
        display_name: str, password: str,
    ) -> str:
        """Self-registration path (spec 015 D-AUTH-04).

        Creates the user with a PERMANENT password chosen by the user
        (``temporary=false`` — Keycloak does NOT force a change) and NO
        required actions: MFA enrollment is optional and happens inline via
        the OTP form when the app requests acr_values=2 (probe S2/S3).
        The password transits once towards Admin REST and is never stored.

        Raises ``KeycloakAdminError(step='keycloak.self_register.exists')``
        when the username already exists (caller maps it to 409).
        Returns the new user's uuid.
        """
        payload = {
            "username": username,
            "enabled": True,
            "email": email,
            "firstName": (display_name or username).split(" ")[0],
            "lastName": " ".join((display_name or username).split(" ")[1:])
            or "User",
            # Dev-local v1 (spec 015 §4): no email verification round-trip.
            "emailVerified": True,
            "requiredActions": [],
            "attributes": {"tenant_id": [tenant_id]},
        }
        if self.find_user(realm, username) is not None:
            raise KeycloakAdminError(
                "keycloak.self_register.exists",
                f"user '{username}' already exists in realm '{realm}'",
            )
        resp = self._request(
            "POST", f"/admin/realms/{realm}/users",
            step="keycloak.self_register", json_body=payload,
        )
        if resp.status_code not in (201,):
            raise KeycloakAdminError(
                "keycloak.self_register",
                f"create user: HTTP {resp.status_code} {resp.text[:200]}",
            )
        created = self.find_user(realm, username)
        if created is None:
            raise KeycloakAdminError(
                "keycloak.self_register", "user missing after create"
            )
        resp = self._request(
            "PUT",
            f"/admin/realms/{realm}/users/{created['id']}/reset-password",
            step="keycloak.self_register",
            json_body={"type": "password", "value": password,
                       "temporary": False},
        )
        if resp.status_code != 204:
            raise KeycloakAdminError(
                "keycloak.self_register",
                f"set password: HTTP {resp.status_code} {resp.text[:200]}",
            )
        return created["id"]

    def ensure_mfa_acr(self, realm: str) -> None:
        """Wire the OPTIONAL-MFA browser flow (spec 015 D-AUTH-06, idempotent).

        Probe-verified design (scripts/probe_loa_flow.py, KC 26.2.5,
        findings in docs/evidencia/auth-ux/probe-loa-findings.txt):

          1. realm attribute `acr.loa.map` = {"1":1,"2":2}
          2. a copy of the built-in `browser` flow restructured as
                 forms [ALTERNATIVE]
                   ├── "Password LoA" [CONDITIONAL, priority 15]
                   │     ├── Condition - LoA level 1 [REQUIRED]
                   │     └── Username Password Form [REQUIRED]
                   └── "… Conditional OTP" [CONDITIONAL, priority 20]
                         ├── Condition - LoA level 2 [REQUIRED]
                         └── OTP Form [REQUIRED]
             ("Condition - user configured" is DELETED from the OTP sub-flow:
             with both conditions Keycloak grants the LoA when the condition
             merely matches, even if the flow is skipped — broken acr.)
          3. the copy bound as the realm's browserFlow.

        Result (S1..S8): acr_values=1 → password-only → `acr:"1"` (no OTP);
        acr_values=2 → OTP form (inline CONFIGURE_TOTP enrollment for users
        without a TOTP credential) → `acr:"2"`; SSO step-up preserved via
        loa-max-age.
        """
        # -- 1) realm attribute ------------------------------------------------
        rep = self._get_realm_rep(realm, "keycloak.mfa_acr")
        attrs = dict(rep.get("attributes") or {})
        if attrs.get(ACR_LOA_MAP_ATTR) != ACR_LOA_MAP_JSON:
            attrs[ACR_LOA_MAP_ATTR] = ACR_LOA_MAP_JSON
            rep["attributes"] = attrs
            resp = self._request(
                "PUT", f"/admin/realms/{realm}", step="keycloak.mfa_acr",
                json_body=rep,
            )
            if resp.status_code not in (200, 204):
                raise KeycloakAdminError(
                    "keycloak.mfa_acr",
                    f"PUT realm acr.loa.map: HTTP {resp.status_code} {resp.text[:200]}",
                )

        # -- 2) browser flow restructure ---------------------------------------
        if rep.get("browserFlow") == BROWSER_LOA_FLOW and \
                self._loa_flow_ready(realm):
            return
        resp = self._request(
            "POST", f"/admin/realms/{realm}/authentication/flows/browser/copy",
            step="keycloak.mfa_acr", json_body={"newName": BROWSER_LOA_FLOW},
        )
        if resp.status_code not in (201, 409):
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"copy browser flow: HTTP {resp.status_code} {resp.text[:200]}",
            )
        self._restructure_loa_flow(realm)
        # -- 3) bind the copy as the realm's browserFlow ----------------------
        resp = self._request(
            "PUT", f"/admin/realms/{realm}", step="keycloak.mfa_acr",
            json_body={"browserFlow": BROWSER_LOA_FLOW},
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"bind browserFlow: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def _get_realm_rep(self, realm: str, step: str) -> dict:
        resp = self._request("GET", f"/admin/realms/{realm}", step=step)
        if resp.status_code != 200:
            raise KeycloakAdminError(step, f"GET realm: HTTP {resp.status_code}")
        return resp.json()

    def _restructure_loa_flow(self, realm: str) -> None:
        """Bring the copied flow to the D-AUTH-06 shape (idempotent: every
        step checks before mutating, so re-running on an already-restructured
        or OLD-wired (spec 011) flow converges)."""
        flat = self._flow_executions(realm, BROWSER_LOA_FLOW, "keycloak.mfa_acr")
        forms = next(
            (e for e in flat
             if (e.get("displayName") or "").endswith(" forms")), None,
        )
        otp = next(
            (e for e in flat
             if "Conditional OTP" in (e.get("displayName") or "")), None,
        )
        if forms is None or otp is None:
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"flow '{BROWSER_LOA_FLOW}' is missing its forms/OTP sub-flows",
            )
        forms_alias = forms["displayName"]
        otp_alias = otp["displayName"]

        # -- 2a) "Password LoA" conditional sub-flow inside forms -------------
        pwd = next(
            (e for e in flat
             if PASSWORD_LOA_SUBFLOW in (e.get("displayName") or "")), None,
        )
        if pwd is None:
            # Probe finding: POST /flows/{parentAlias}/executions/flow with
            # providerId="basic-flow"; the creation IGNORES requirement and
            # priority (set right after via PUT on the flat listing entry).
            resp = self._request(
                "POST",
                f"/admin/realms/{realm}/authentication/flows/"
                f"{forms_alias}/executions/flow",
                step="keycloak.mfa_acr",
                json_body={
                    "alias": PASSWORD_LOA_SUBFLOW,
                    "description": "password wrapper (LoA 1)",
                    "providerId": "basic-flow",
                    "type": "basic-flow",
                    "requirement": "CONDITIONAL",
                },
            )
            if resp.status_code not in (201, 409):
                raise KeycloakAdminError(
                    "keycloak.mfa_acr",
                    f"create '{PASSWORD_LOA_SUBFLOW}' sub-flow: "
                    f"HTTP {resp.status_code} {resp.text[:200]}",
                )
            flat = self._flow_executions(realm, BROWSER_LOA_FLOW, "keycloak.mfa_acr")
            pwd = next(
                (e for e in flat
                 if PASSWORD_LOA_SUBFLOW in (e.get("displayName") or "")), None,
            )
            if pwd is None:
                raise KeycloakAdminError(
                    "keycloak.mfa_acr",
                    f"'{PASSWORD_LOA_SUBFLOW}' missing after create",
                )
        if pwd.get("requirement") != "CONDITIONAL" or \
                pwd.get("priority") != PASSWORD_LOA_PRIORITY:
            entry = dict(pwd)
            entry["requirement"] = "CONDITIONAL"
            entry["priority"] = PASSWORD_LOA_PRIORITY
            resp = self._request(
                "PUT",
                f"/admin/realms/{realm}/authentication/flows/"
                f"{BROWSER_LOA_FLOW}/executions",
                step="keycloak.mfa_acr", json_body=entry,
            )
            if resp.status_code not in (200, 204):
                raise KeycloakAdminError(
                    "keycloak.mfa_acr",
                    f"set requirement/priority of '{PASSWORD_LOA_SUBFLOW}': "
                    f"HTTP {resp.status_code} {resp.text[:200]}",
                )

        # -- 2b) LoA condition level 1 + username/password INSIDE the wrapper -
        pwd_alias = pwd["displayName"]
        pwd_id = pwd["flowId"]
        self._ensure_loa_condition(realm, pwd_id, pwd_alias, level="1",
                                   config_alias="trust-loa-password-level-1")
        pwd_execs = self._flow_executions(realm, pwd_alias, "keycloak.mfa_acr")
        if not any(e.get("providerId") == USERNAME_PASSWORD_PROVIDER
                   for e in pwd_execs):
            self._add_execution(realm, pwd_id, USERNAME_PASSWORD_PROVIDER,
                                "REQUIRED", 20)
            # Re-fetch: the new execution's id must be part of the ownership
            # set below (pwd_execs was captured before the add).
            pwd_execs = self._flow_executions(
                realm, pwd_alias, "keycloak.mfa_acr")
        # ... and REMOVE the original copy left directly under `forms`.
        # The executions listing is NESTED-INCLUSIVE (it also returns the
        # entries of sub-flows), so the freshly added username/password form
        # inside 'Password LoA' shows up here too — only delete entries NOT
        # owned by the Password LoA sub-flow (authenticator executions carry
        # flowId=None, so ownership is decided by membership, live-verified).
        pwd_exec_ids = {e["id"] for e in pwd_execs}
        for execution in self._flow_executions(realm, forms_alias, "keycloak.mfa_acr"):
            if execution.get("providerId") == USERNAME_PASSWORD_PROVIDER \
                    and execution.get("id") not in pwd_exec_ids:
                resp = self._request(
                    "DELETE",
                    f"/admin/realms/{realm}/authentication/executions/"
                    f"{execution['id']}",
                    step="keycloak.mfa_acr",
                )
                if resp.status_code not in (200, 204, 404):
                    raise KeycloakAdminError(
                        "keycloak.mfa_acr",
                        f"move username/password form into '{PASSWORD_LOA_SUBFLOW}': "
                        f"HTTP {resp.status_code} {resp.text[:200]}",
                    )

        # -- 2c) Conditional OTP: LoA level 2, NO user-configured -------------
        self._remove_user_configured_condition(realm, otp_alias)
        self._ensure_loa_condition(realm, otp["flowId"], otp_alias, level="2",
                                   config_alias="trust-loa-otp-level-2")

    def _flow_executions(self, realm: str, flow_alias: str, step: str) -> list:
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/authentication/flows/{flow_alias}/executions",
            step=step,
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                step, f"list executions of '{flow_alias}': HTTP {resp.status_code}"
            )
        return resp.json()

    @staticmethod
    def _execution_config_id(execution: dict) -> str | None:
        # KC 26 lists the linked config as "authenticationConfig" (the
        # AuthenticationExecutionRepresentation field is "authenticatorConfig"
        # — the executions listing uses yet another name; accept both).
        return (execution.get("authenticationConfig")
                or execution.get("authenticatorConfig"))

    def _loa_condition_level(self, realm: str, execution: dict) -> str | None:
        """Configured loa-condition-level of a LoA condition execution."""
        config_id = self._execution_config_id(execution)
        if not config_id:
            return None
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/authentication/config/{config_id}",
            step="keycloak.mfa_acr",
        )
        if resp.status_code != 200:
            return None
        config = resp.json().get("config") or {}
        return config.get("loa-condition-level")

    def _subflow_loa_state(self, realm: str, subflow_alias: str) -> dict:
        """-> {"loa_level": str|None, "has_user_configured": bool} for one
        sub-flow of the bound browser flow (never raises)."""
        state = {"loa_level": None, "has_user_configured": False}
        try:
            executions = self._flow_executions(
                realm, subflow_alias, "keycloak.mfa_acr")
        except KeycloakAdminError:
            return state
        for execution in executions:
            provider = execution.get("providerId")
            if provider == USER_CONFIGURED_PROVIDER:
                state["has_user_configured"] = True
            elif provider == LOA_CONDITION_PROVIDER:
                state["loa_level"] = self._loa_condition_level(realm, execution)
        return state

    def _loa_flow_ready(self, realm: str) -> bool:
        """True when the bound flow already has the D-AUTH-06 shape:
        Password LoA (LoA@1 + username/password form, CONDITIONAL, correct
        priority) and Conditional OTP (LoA@2, no user-configured)."""
        try:
            flat = self._flow_executions(
                realm, BROWSER_LOA_FLOW, "keycloak.mfa_acr")
        except KeycloakAdminError:
            return False
        pwd = next(
            (e for e in flat
             if PASSWORD_LOA_SUBFLOW in (e.get("displayName") or "")), None,
        )
        otp = next(
            (e for e in flat
             if "Conditional OTP" in (e.get("displayName") or "")), None,
        )
        if pwd is None or otp is None:
            return False
        if pwd.get("requirement") != "CONDITIONAL" or \
                pwd.get("priority") != PASSWORD_LOA_PRIORITY:
            return False
        pwd_state = self._subflow_loa_state(realm, pwd["displayName"])
        if pwd_state["loa_level"] != "1":
            return False
        try:
            pwd_execs = self._flow_executions(
                realm, pwd["displayName"], "keycloak.mfa_acr")
        except KeycloakAdminError:
            return False
        if not any(e.get("providerId") == USERNAME_PASSWORD_PROVIDER
                   for e in pwd_execs):
            return False
        otp_state = self._subflow_loa_state(realm, otp["displayName"])
        return otp_state["loa_level"] == "2" and not otp_state["has_user_configured"]

    def _add_execution(
        self, realm: str, flow_id: str, authenticator: str,
        requirement: str, priority: int,
    ) -> None:
        # NOTE (verified live): the representation field is "authenticator"
        # (not "provider"), requirement must be explicit (a missing one 500s
        # with NPE) and parentFlow must be set.
        resp = self._request(
            "POST",
            f"/admin/realms/{realm}/authentication/executions?flow={flow_id}",
            step="keycloak.mfa_acr",
            json_body={
                "authenticator": authenticator,
                "requirement": requirement,
                "priority": priority,
                "parentFlow": flow_id,
            },
        )
        if resp.status_code not in (201, 204):
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"add execution {authenticator}: HTTP {resp.status_code} "
                f"{resp.text[:200]}",
            )

    def _ensure_loa_condition(
        self, realm: str, subflow_id: str, subflow_alias: str, *,
        level: str, config_alias: str | None = None,
    ) -> None:
        """The sub-flow carries exactly one LoA condition configured at
        `level` (adds it, or re-configures an existing one — old spec-011
        realms have level 1 on the OTP sub-flow)."""
        for execution in self._flow_executions(realm, subflow_alias, "keycloak.mfa_acr"):
            if execution.get("providerId") != LOA_CONDITION_PROVIDER:
                continue
            if self._loa_condition_level(realm, execution) != level:
                self._set_loa_config(realm, execution["id"], level,
                                     alias=config_alias)
            return
        self._add_execution(realm, subflow_id, LOA_CONDITION_PROVIDER,
                            "REQUIRED", 10)
        for execution in self._flow_executions(realm, subflow_alias, "keycloak.mfa_acr"):
            if execution.get("providerId") == LOA_CONDITION_PROVIDER:
                self._set_loa_config(realm, execution["id"], level,
                                     alias=config_alias)
                return
        raise KeycloakAdminError(
            "keycloak.mfa_acr", "LoA condition missing after create"
        )

    def _remove_user_configured_condition(self, realm: str, subflow_alias: str) -> None:
        """Delete 'Condition - user configured' from a sub-flow (see
        USER_CONFIGURED_PROVIDER). The copy is not built-in, so
        DELETE /executions/{id} is allowed."""
        for execution in self._flow_executions(realm, subflow_alias, "keycloak.mfa_acr"):
            if execution.get("providerId") != USER_CONFIGURED_PROVIDER:
                continue
            resp = self._request(
                "DELETE",
                f"/admin/realms/{realm}/authentication/executions/{execution['id']}",
                step="keycloak.mfa_acr",
            )
            if resp.status_code not in (200, 204, 404):
                raise KeycloakAdminError(
                    "keycloak.mfa_acr",
                    f"remove user-configured condition: HTTP {resp.status_code} "
                    f"{resp.text[:200]}",
                )
            return

    def _set_loa_config(self, realm: str, execution_id: str, level: str,
                        alias: str | None = None) -> None:
        """Config payload is wrapped: {"alias": ..., "config": {...}}.

        The alias is COSMETIC (every read path resolves the config by the
        execution's authenticationConfig id, never by alias) but must be
        unique per realm. Old spec-011 realms already own a config aliased
        `trust-loa-level-1` on the OTP condition, so a fresh Password-LoA
        config with that alias collides (HTTP 409). On conflict the alias is
        uniquified with the execution id — the level is what matters.
        """
        base_alias = alias or f"trust-loa-level-{level}"
        body = {
            "alias": base_alias,
            "config": {
                "loa-condition-level": level,
                "loa-max-age": LOA_CONDITION_MAX_AGE,
            },
        }
        resp = self._request(
            "POST",
            f"/admin/realms/{realm}/authentication/executions/{execution_id}/config",
            step="keycloak.mfa_acr", json_body=body,
        )
        if resp.status_code == 404:  # config already exists -> replace it
            resp = self._request(
                "PUT",
                f"/admin/realms/{realm}/authentication/executions/{execution_id}/config",
                step="keycloak.mfa_acr", json_body=body,
            )
        elif resp.status_code == 409:
            # Alias owned by another execution's config (old spec-011
            # wiring): retry once with a uniquified alias.
            body["alias"] = f"{base_alias}-{execution_id[:8]}"
            resp = self._request(
                "POST",
                f"/admin/realms/{realm}/authentication/executions/{execution_id}/config",
                step="keycloak.mfa_acr", json_body=body,
            )
            if resp.status_code == 404:
                resp = self._request(
                    "PUT",
                    f"/admin/realms/{realm}/authentication/executions/{execution_id}/config",
                    step="keycloak.mfa_acr", json_body=body,
                )
        if resp.status_code not in (201, 204):
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"set LoA condition config (level {level}): "
                f"HTTP {resp.status_code} {resp.text[:200]}",
            )

    # -- white-label theme + Google brokering (spec 015) -----------------------

    def ensure_login_theme(self, realm: str, product_name: str | None = None) -> None:
        """Bind the `trust-agent` login theme and brand the realm header
        (D-AUTH-01, idempotent). keycloak.v2 renders the realm's
        displayNameHtml as the login page header, so the white-label brand is
        data, not a template fork."""
        rep = self._get_realm_rep(realm, "keycloak.login_theme")
        display = product_name or "Trust Agent"
        # Plain text: keycloak.v2 renders the header through kcSanitize —
        # no HTML needed, the pf-v5-c-brand class styles it.
        display_html = display
        updates: dict = {}
        if rep.get("loginTheme") != LOGIN_THEME:
            updates["loginTheme"] = LOGIN_THEME
        if rep.get("displayNameHtml") != display_html:
            updates["displayNameHtml"] = display_html
        # keycloak.v2 also renders `Sign in to ${realm.displayName}` as the
        # page title/lead — keep the internal slug out of the white-label
        # surface (only override when it is unset or still the raw slug,
        # never clobber a deliberate custom display name).
        if rep.get("displayName") in (None, "", realm):
            updates["displayName"] = display
        if updates:
            rep.update(updates)
            resp = self._request(
                "PUT", f"/admin/realms/{realm}", step="keycloak.login_theme",
                json_body=rep,
            )
            if resp.status_code not in (200, 204):
                raise KeycloakAdminError(
                    "keycloak.login_theme",
                    f"bind login theme: HTTP {resp.status_code} {resp.text[:200]}",
                )
        # Per-tenant message overrides (t_e68687ee): the TOTP enrollment
        # h1 carries the tenant's product name. Theme bundles cannot do this
        # (login-config-totp.ftl calls msg("loginTotpTitle") with no args),
        # so the brand rides realm localization texts — data, not a fork.
        self._ensure_localization(realm, self._brand_localization(display))

    # --- realm localization texts (per-tenant message overrides) --------------

    def _brand_localization(self, product: str) -> dict[str, str]:
        """Message overrides that embed the tenant product name. Keys not
        listed here fall back to the theme's messages_*.properties (generic
        white-label copy) and then to the KC defaults."""
        return {TOTP_TITLE_KEY: f"Secure your {product} account"}

    def _ensure_localization(self, realm: str, texts: dict[str, str]) -> None:
        """Idempotently merge `texts` into the realm's `en` localization
        overrides. KC 26 pitfall (verified live): PUT /admin/realms/{r}
        silently DROPS `localizationTexts` — the dedicated endpoints are
        GET/POST /admin/realms/{r}/localization/{locale} (POST merges
        per-key) and they work with internationalization disabled."""
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/localization/{LOCALIZATION_LOCALE}",
            step="keycloak.login_theme",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.login_theme",
                f"get localization texts: HTTP {resp.status_code} {resp.text[:200]}",
            )
        current = resp.json() or {}
        if all(current.get(key) == value for key, value in texts.items()):
            return
        resp = self._request(
            "POST",
            f"/admin/realms/{realm}/localization/{LOCALIZATION_LOCALE}",
            step="keycloak.login_theme",
            json_body=texts,
        )
        if resp.status_code not in (200, 201, 204):
            raise KeycloakAdminError(
                "keycloak.login_theme",
                f"set localization texts: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def _idp_instances(self, realm: str) -> list:
        resp = self._request(
            "GET", f"/admin/realms/{realm}/identity-provider/instances",
            step="keycloak.google_idp",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.google_idp",
                f"list identity providers: HTTP {resp.status_code}",
            )
        return resp.json()

    def ensure_google_idp(self, realm: str) -> bool:
        """Add/update the `google` identity provider (D-AUTH-02, idempotent).

        Fail-closed: without TRUST_GOOGLE_CLIENT_ID/SECRET in the environment
        NO identity provider is added and False is returned (org-config then
        reports google_login_enabled=false). Redirect URI the operator must
        register in Google Cloud Console (per realm):
        {kc-base}/realms/{realm}/broker/google/endpoint.
        """
        client_id = self.settings.google_client_id
        client_secret = self.settings.google_client_secret
        if not client_id or not client_secret:
            return False
        body = {
            "alias": GOOGLE_IDP_ALIAS,
            "providerId": GOOGLE_IDP_ALIAS,
            "enabled": True,
            "trustEmail": True,
            "firstBrokerLoginFlowAlias": "first broker login",
            "config": {
                "clientId": client_id,
                "clientSecret": client_secret,
            },
        }
        existing = next(
            (i for i in self._idp_instances(realm)
             if i.get("alias") == GOOGLE_IDP_ALIAS), None,
        )
        if existing is None:
            resp = self._request(
                "POST",
                f"/admin/realms/{realm}/identity-provider/instances",
                step="keycloak.google_idp", json_body=body,
            )
            if resp.status_code not in (201, 409):
                raise KeycloakAdminError(
                    "keycloak.google_idp",
                    f"create google IdP: HTTP {resp.status_code} {resp.text[:200]}",
                )
            return True
        if (existing.get("config") or {}).get("clientId") == client_id and \
                existing.get("enabled"):
            return True
        body["internalId"] = existing.get("internalId")
        resp = self._request(
            "PUT",
            f"/admin/realms/{realm}/identity-provider/instances/"
            f"{existing['internalId']}",
            step="keycloak.google_idp", json_body=body,
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.google_idp",
                f"update google IdP: HTTP {resp.status_code} {resp.text[:200]}",
            )
        return True

    def google_idp_enabled(self, realm: str) -> bool:
        """Read-only check for org-config (never raises)."""
        try:
            return any(
                i.get("alias") == GOOGLE_IDP_ALIAS and i.get("enabled")
                for i in self._idp_instances(realm)
            )
        except KeycloakAdminError:
            return False

    def ensure_tenant_id_hardcode_mapper(self, realm: str, tenant_id: str) -> None:
        """Hardcode the signed tenant_id claim on the web client (D-AUTH-03).

        Brokered users (auto-created by "Create User If Unique") carry NO user
        attributes, so the attribute mapper alone would not emit tenant_id for
        them and the session mint would fail. The value is constant per realm
        (one realm per tenant) and coexists with the attribute mapper."""
        client_uuid = self._web_client_uuid(realm)
        resp = self._request(
            "GET",
            f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models",
            step="keycloak.mapper",
        )
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.mapper", f"list mappers: HTTP {resp.status_code}"
            )
        mappers = resp.json()
        existing = next(
            (m for m in mappers if m.get("name") == HARDCODE_MAPPER_NAME), None,
        )
        body = {
            "name": HARDCODE_MAPPER_NAME,
            "protocol": "openid-connect",
            # Verified against KC 26.2.5 source (HardcodedClaim.java): the
            # SPI id is "oidc-hardcoded-claim-mapper" — "oidc-hardcoded-claim"
            # and "oidc-hardcode-claim" 404 with "ProtocolMapper provider not
            # found" (live-verified).
            "protocolMapper": "oidc-hardcoded-claim-mapper",
            "config": {
                "claim.name": "tenant_id",
                "claim.value": tenant_id,
                "jsonType.label": "String",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
            },
        }
        if existing is None:
            resp = self._request(
                "POST",
                f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models",
                step="keycloak.mapper", json_body=body,
            )
            if resp.status_code not in (201, 409):
                raise KeycloakAdminError(
                    "keycloak.mapper",
                    f"create hardcode mapper: HTTP {resp.status_code} {resp.text[:200]}",
                )
            return
        if (existing.get("config") or {}).get("claim.value") == tenant_id:
            return
        # Verified live: the update endpoint carries the model id in the
        # path (PUT on the collection 404s) — needed when a realm survives
        # across runs (KC realms persist; the platform DB does not).
        body["id"] = existing["id"]
        resp = self._request(
            "PUT",
            f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/"
            f"models/{existing['id']}",
            step="keycloak.mapper", json_body=body,
        )
        if resp.status_code not in (200, 204):
            raise KeycloakAdminError(
                "keycloak.mapper",
                f"update hardcode mapper: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def get_user(self, realm: str, user_id: str) -> dict | None:
        """Full user representation (includes federatedIdentities) — used by
        the brokered auto-provision path (D-AUTH-05)."""
        resp = self._request(
            "GET", f"/admin/realms/{realm}/users/{user_id}",
            step="keycloak.user_lookup",
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.user_lookup", f"GET user: HTTP {resp.status_code}"
            )
        return resp.json()

    def mfa_acr_state(self, realm: str) -> dict:
        """Read-only MFA/ACR state for healthchecks (never raises)."""
        state = {
            "acr_loa_map": False, "browser_flow": None,
            "loa_flow_ready": False,
        }
        try:
            rep = self._get_realm_rep(realm, "keycloak.mfa_acr")
            state["acr_loa_map"] = (
                (rep.get("attributes") or {}).get(ACR_LOA_MAP_ATTR)
                == ACR_LOA_MAP_JSON
            )
            state["browser_flow"] = rep.get("browserFlow")
            if state["browser_flow"] == BROWSER_LOA_FLOW:
                state["loa_flow_ready"] = self._loa_flow_ready(realm)
        except KeycloakAdminError:
            pass
        return state

    def delete_realm(self, realm: str) -> None:
        """Compensating action (rollback): remove the whole realm."""
        resp = self._request(
            "DELETE", f"/admin/realms/{realm}", step="keycloak.rollback"
        )
        if resp.status_code not in (200, 204, 404):
            raise KeycloakAdminError(
                "keycloak.rollback",
                f"delete realm: HTTP {resp.status_code} {resp.text[:200]}",
            )
