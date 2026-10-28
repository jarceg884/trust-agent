"""Unit tests — per-tenant realm localization overrides (t_e68687ee, spec 015).

KeycloakAdminClient.ensure_login_theme must also converge the realm's `en`
localization texts so the TOTP enrollment h1 carries the tenant branding
product_name ("Secure your <product> account") instead of the KC default
"Mobile Authenticator Setup". The HTTP layer is stubbed here — the live
convergence is covered by test_provision (real Keycloak) and the browser
evidence run.
"""
from app.tenants.keycloak_admin import (
    LOCALIZATION_LOCALE,
    TOTP_TITLE_KEY,
    KeycloakAdminClient,
)


def _stub_kc(localization=None):
    """A real KeycloakAdminClient with the HTTP layer replaced by a recorder
    that emulates the KC 26 localization endpoints (GET returns the stored
    map, POST merges per-key). Returns (client, state) where state carries
    the stored localization map and every recorded call."""
    kc = KeycloakAdminClient.__new__(KeycloakAdminClient)
    state = {"loc": dict(localization or {}), "calls": []}

    def _get_realm_rep(realm, step):
        # Already-converged realm rep: no PUT should ever be issued.
        return {"loginTheme": "trust-agent", "displayName": "Trust Agent",
                "displayNameHtml": "Trust Agent"}

    def _request(method, path, *, step, json_body=None, retry_auth=True):
        state["calls"].append((method, path, json_body))
        suffix = f"/localization/{LOCALIZATION_LOCALE}"
        if method == "PUT" and path.startswith("/admin/realms/"):
            return type("Resp", (), {"status_code": 204})()
        if method == "GET" and path.endswith(suffix):
            return type("Resp", (), {
                "status_code": 200, "json": lambda self: dict(state["loc"]),
            })()
        if method == "POST" and path.endswith(suffix):
            assert json_body, "POST localization without a body"
            state["loc"].update(json_body)
            return type("Resp", (), {"status_code": 204})()
        raise AssertionError(f"unexpected request: {method} {path}")

    kc.settings = None  # type: ignore[assignment]
    kc._token = "stub"  # type: ignore[attr-defined]
    kc._get_realm_rep = _get_realm_rep  # type: ignore[method-assign]
    kc._request = _request  # type: ignore[method-assign]
    return kc, state


def _posts(state):
    return [c for c in state["calls"] if c[0] == "POST"]


def test_brand_localization_embeds_product_name():
    kc, _ = _stub_kc()
    assert kc._brand_localization("Acme Robotics") == {
        TOTP_TITLE_KEY: "Secure your Acme Robotics account"
    }


def test_ensure_login_theme_defaults_to_trust_agent():
    kc, state = _stub_kc()
    kc.ensure_login_theme("some-realm")
    assert state["loc"] == {TOTP_TITLE_KEY: "Secure your Trust Agent account"}


def test_ensure_login_theme_converges_stale_override():
    kc, state = _stub_kc({TOTP_TITLE_KEY: "Mobile Authenticator Setup"})
    kc.ensure_login_theme("some-realm", product_name="Acme")
    assert state["loc"] == {TOTP_TITLE_KEY: "Secure your Acme account"}
    assert len(_posts(state)) == 1


def test_ensure_localization_idempotent_no_write_when_current():
    # Default product matches the stub's realm rep: no PUT, and the
    # localization already matches: no POST either — just the GET.
    kc, state = _stub_kc({TOTP_TITLE_KEY: "Secure your Trust Agent account"})
    kc.ensure_login_theme("some-realm")
    assert _posts(state) == []
    puts = [c for c in state["calls"] if c[0] == "PUT"]
    assert puts == []
    assert state["calls"][0][0] == "GET"


def test_ensure_localization_preserves_foreign_keys():
    # POST merges per-key: keys we do not manage must survive a converge.
    kc, state = _stub_kc({TOTP_TITLE_KEY: "stale", "customKey": "keep-me"})
    kc.ensure_login_theme("some-realm", product_name="Acme")
    assert state["loc"]["customKey"] == "keep-me"
    assert state["loc"][TOTP_TITLE_KEY] == "Secure your Acme account"
    assert _posts(state)[0][2] == {TOTP_TITLE_KEY: "Secure your Acme account"}
