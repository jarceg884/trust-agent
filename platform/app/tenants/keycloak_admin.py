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
ACR_LOA_MAP_JSON = '{"1":0,"2":1}'
# The built-in "browser" flow cannot be modified ("illegal to add execution
# to a built in flow"), so a copy is created and bound as the realm's
# browserFlow.
BROWSER_LOA_FLOW = "trust-browser-loa"
LOA_CONDITION_PROVIDER = "conditional-level-of-authentication"
LOA_CONDITION_LEVEL = "1"
LOA_CONDITION_MAX_AGE = "36000"
# The copied "… Conditional OTP" sub-flow must keep ONLY the LoA condition:
# with BOTH "Condition - user configured" and the LoA condition present,
# Keycloak raises the session LoA when the LoA condition merely MATCHES —
# even if the flow is then SKIPPED for users without OTP (verified live:
# password-only logins got acr:"2"). With the LoA condition alone the only
# path to LoA 1 is the OTP form, and for users without a TOTP credential
# OTPFormAuthenticator.setRequiredActions() forces inline CONFIGURE_TOTP
# enrollment — so nobody is locked out and nobody gets acr:"2" without a
# second factor.
USER_CONFIGURED_PROVIDER = "conditional-user-configured"


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
            return clients[0]["id"]
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
                "directAccessGrantsEnabled": False,
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

    def ensure_admin_user(
        self, realm: str, *, username: str, email: str, tenant_id: str,
        display_name: str,
    ) -> tuple[str, str]:
        """Create (or re-assert) the initial org_admin user.

        Returns (user_uuid, one_time_password). The password is TEMPORARY:
        Keycloak forces a change on first login, and CONFIGURE_TOTP forces
        TOTP enrollment (REQ-ONB-004). The one-time password is generated
        with secrets and returned exactly once — never stored in our DB.
        """
        payload = {
            "username": username,
            "enabled": True,
            "email": email,
            "firstName": (display_name or "Admin").split(" ")[0],
            "lastName": " ".join((display_name or "Admin").split(" ")[1:]) or "Admin",
            "emailVerified": True,
            "requiredActions": ["CONFIGURE_TOTP"],
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

    def ensure_mfa_acr(self, realm: str) -> None:
        """Wire ACR/LoA so OTP logins carry `acr:"2"` (D-MFA-01, idempotent).

        Keycloak 26 never emits `amr`, so the platform's MFA gate reads
        `acr` (app/auth/mfa.py). This step makes the realm produce it:

          1. realm attribute `acr.loa.map` = {"1":0,"2":1}
          2. a copy of the built-in `browser` flow whose "… Conditional OTP"
             sub-flow carries ONLY a REQUIRED "Condition - Level of
             Authentication" (level 1) next to the OTP form — the copied
             "Condition - user configured" is DELETED (with both conditions
             Keycloak grants the LoA when the condition merely matches, even
             if the flow is skipped for users without OTP: password-only
             logins got acr:"2" — verified live)
          3. the copy bound as the realm's browserFlow.

        Result (verified live against KC 26.2): password+TOTP → LoA 1 →
        `acr:"2"` in id_token and access_token; password-only → the OTP form
        (inline CONFIGURE_TOTP enrollment for users without a TOTP credential
        via OTPFormAuthenticator.setRequiredActions) and never `acr:"2"`.
        """
        # -- 1) realm attribute ------------------------------------------------
        resp = self._request("GET", f"/admin/realms/{realm}", step="keycloak.mfa_acr")
        if resp.status_code != 200:
            raise KeycloakAdminError(
                "keycloak.mfa_acr", f"GET realm: HTTP {resp.status_code}"
            )
        rep = resp.json()
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

        # -- 2) browser flow with a LoA condition on Conditional OTP ----------
        if rep.get("browserFlow") == BROWSER_LOA_FLOW and \
                self._loa_condition_configured(realm):
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
        self._ensure_loa_condition(realm)
        self._remove_user_configured_condition(realm)
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

    def _conditional_otp_subflow(self, realm: str) -> tuple[str, str]:
        """-> (subflow_id, subflow_alias) inside the bound browser flow."""
        executions = self._flow_executions(realm, BROWSER_LOA_FLOW, "keycloak.mfa_acr")
        for execution in executions:
            if "Conditional OTP" in (execution.get("displayName") or ""):
                return execution["flowId"], execution["displayName"]
        raise KeycloakAdminError(
            "keycloak.mfa_acr",
            f"no 'Conditional OTP' sub-flow in flow '{BROWSER_LOA_FLOW}'",
        )

    @staticmethod
    def _execution_config_id(execution: dict) -> str | None:
        # KC 26 lists the linked config as "authenticationConfig" (the
        # AuthenticationExecutionRepresentation field is "authenticatorConfig"
        # — the executions listing uses yet another name; accept both).
        return (execution.get("authenticationConfig")
                or execution.get("authenticatorConfig"))

    def _loa_condition_configured(self, realm: str) -> bool:
        """True when the Conditional OTP sub-flow carries the LoA condition
        with the expected level AND no 'Condition - user configured' (see
        USER_CONFIGURED_PROVIDER: both conditions together grant the LoA
        even when the flow is skipped — broken acr)."""
        try:
            _, subflow_alias = self._conditional_otp_subflow(realm)
        except KeycloakAdminError:
            return False
        loa_ok = False
        for execution in self._flow_executions(realm, subflow_alias, "keycloak.mfa_acr"):
            provider = execution.get("providerId")
            if provider == USER_CONFIGURED_PROVIDER:
                return False
            if provider != LOA_CONDITION_PROVIDER:
                continue
            config_id = self._execution_config_id(execution)
            if not config_id:
                return False
            resp = self._request(
                "GET",
                f"/admin/realms/{realm}/authentication/config/{config_id}",
                step="keycloak.mfa_acr",
            )
            if resp.status_code != 200:
                return False
            config = resp.json().get("config") or {}
            loa_ok = config.get("loa-condition-level") == LOA_CONDITION_LEVEL
        return loa_ok

    def _ensure_loa_condition(self, realm: str) -> None:
        subflow_id, subflow_alias = self._conditional_otp_subflow(realm)
        for execution in self._flow_executions(realm, subflow_alias, "keycloak.mfa_acr"):
            if execution.get("providerId") == LOA_CONDITION_PROVIDER:
                if not self._execution_config_id(execution):
                    self._set_loa_config(realm, execution["id"])
                return
        # Not present: add it. NOTE (verified live): the representation field
        # is "authenticator" (not "provider"), requirement must be explicit
        # (a missing one 500s with NPE) and parentFlow must be set.
        resp = self._request(
            "POST",
            f"/admin/realms/{realm}/authentication/executions?flow={subflow_id}",
            step="keycloak.mfa_acr",
            json_body={
                "authenticator": LOA_CONDITION_PROVIDER,
                "requirement": "REQUIRED",
                "priority": 20,
                "parentFlow": subflow_id,
            },
        )
        if resp.status_code not in (201, 204):
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"add LoA condition: HTTP {resp.status_code} {resp.text[:200]}",
            )
        for execution in self._flow_executions(realm, subflow_alias, "keycloak.mfa_acr"):
            if execution.get("providerId") == LOA_CONDITION_PROVIDER:
                self._set_loa_config(realm, execution["id"])
                return
        raise KeycloakAdminError(
            "keycloak.mfa_acr", "LoA condition missing after create"
        )

    def _remove_user_configured_condition(self, realm: str) -> None:
        """Delete 'Condition - user configured' from the Conditional OTP
        sub-flow (see USER_CONFIGURED_PROVIDER). The copy is not built-in,
        so DELETE /executions/{id} is allowed."""
        try:
            _, subflow_alias = self._conditional_otp_subflow(realm)
        except KeycloakAdminError:
            return
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

    def _set_loa_config(self, realm: str, execution_id: str) -> None:
        """Config payload is wrapped: {"alias": ..., "config": {...}}."""
        body = {
            "alias": "trust-loa-level-1",
            "config": {
                "loa-condition-level": LOA_CONDITION_LEVEL,
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
        if resp.status_code not in (201, 204):
            raise KeycloakAdminError(
                "keycloak.mfa_acr",
                f"set LoA condition config: HTTP {resp.status_code} {resp.text[:200]}",
            )

    def mfa_acr_state(self, realm: str) -> dict:
        """Read-only MFA/ACR state for healthchecks (never raises)."""
        state = {"acr_loa_map": False, "browser_flow": None, "loa_condition": False}
        try:
            resp = self._request(
                "GET", f"/admin/realms/{realm}", step="keycloak.mfa_acr"
            )
            if resp.status_code == 200:
                rep = resp.json()
                state["acr_loa_map"] = (
                    (rep.get("attributes") or {}).get(ACR_LOA_MAP_ATTR)
                    == ACR_LOA_MAP_JSON
                )
                state["browser_flow"] = rep.get("browserFlow")
            if state["browser_flow"] == BROWSER_LOA_FLOW:
                state["loa_condition"] = self._loa_condition_configured(realm)
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
