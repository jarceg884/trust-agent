"""AC-CP-051 — deny-overrides combinator (unit) + effective policy endpoint."""
import pytest

from app.governance.combinator import combine
from tests.conftest import bearer_headers, mint

CATALOG = {
    "require_mfa": {"value_type": "boolean", "invariable": True,
                    "default_value": True},
    "approved_models_only": {"value_type": "boolean", "invariable": True,
                             "default_value": True},
    "allowed_connectors": {"value_type": "list", "invariable": False,
                           "default_value": []},
    "session_timeout_minutes": {"value_type": "integer", "invariable": False,
                                "default_value": 60},
    "shared_agent_grants_mode": {"value_type": "string", "invariable": False,
                                 "default_value": "explicit"},
    "artifact_sharing": {"value_type": "string", "invariable": False,
                         "default_value": "private"},
    "messaging_permissions": {"value_type": "map", "invariable": False,
                              "default_value": {}},
    "max_daily_sessions": {"value_type": "integer", "invariable": False,
                           "default_value": 10},
}


def test_boolean_deny_overrides():
    # org allows, dept denies -> denied (AC-POL-002 case 2)
    eff = combine({"require_mfa": False}, {}, {}, CATALOG)
    assert eff["require_mfa"] is True  # invariable floor wins anyway
    # non-invariable boolean: any False denies
    cat = dict(CATALOG)
    cat["custom_bool"] = {"value_type": "boolean", "invariable": False,
                          "default_value": True}
    eff = combine({"custom_bool": True}, {"custom_bool": False}, {}, cat)
    assert eff["custom_bool"] is False


def test_org_only_keys_resolve_as_the_gate():
    """Spec 015 (D-AUTH-07/08, hallazgo t_8f716f9f): org-only keys ignore
    dept/user values in combine() — the enforcement gate reads ONLY the
    org level, so the effective policy must mirror it even if a legacy
    dept/user row exists (the upsert now rejects new ones with 422)."""
    cat = dict(CATALOG)
    cat["require_mfa"] = {"value_type": "boolean", "invariable": False,
                          "default_value": False}
    cat["allow_self_registration"] = {"value_type": "boolean",
                                      "invariable": False,
                                      "default_value": True}
    # org=true, dept=false, user=false -> the gate sees true (NOT AND=false)
    eff = combine({"require_mfa": True}, {"require_mfa": False},
                  {"require_mfa": False}, cat)
    assert eff["require_mfa"] is True
    # no org row -> catalog default; dept/user rows cannot change it
    eff = combine({}, {"require_mfa": True}, {"require_mfa": True}, cat)
    assert eff["require_mfa"] is False
    # same semantics for allow_self_registration (registration is org-wide)
    eff = combine({}, {"allow_self_registration": False}, {}, cat)
    assert eff["allow_self_registration"] is True


def test_list_intersection():
    # org allows [vmware,email], dept allows [vmware] -> [vmware]
    eff = combine({"allowed_connectors": ["vmware", "email"]},
                  {"allowed_connectors": ["vmware"]}, {}, CATALOG)
    assert eff["allowed_connectors"] == ["vmware"]
    # org allows nothing -> nothing, dept cannot relax
    eff = combine({"allowed_connectors": []},
                  {"allowed_connectors": ["vmware"]}, {}, CATALOG)
    assert eff["allowed_connectors"] == []


def test_integer_minimum():
    eff = combine({"session_timeout_minutes": 60},
                  {"session_timeout_minutes": 30}, {}, CATALOG)
    assert eff["session_timeout_minutes"] == 30
    eff = combine({}, {}, {"session_timeout_minutes": 15}, CATALOG)
    assert eff["session_timeout_minutes"] == 15


def test_string_rank_most_restrictive():
    eff = combine({"shared_agent_grants_mode": "open"},
                  {"shared_agent_grants_mode": "explicit"}, {}, CATALOG)
    assert eff["shared_agent_grants_mode"] == "explicit"
    eff = combine({"artifact_sharing": "shared"}, {}, {}, CATALOG)
    assert eff["artifact_sharing"] == "shared"


def test_map_messaging_permissions():
    # telegram: org allows reply, user asks full -> reply (most restrictive)
    eff = combine(
        {"messaging_permissions": {"telegram": {"allowed": True, "mode": "reply"}}},
        {},
        {"messaging_permissions": {"telegram": {"allowed": True, "mode": "full"}}},
        CATALOG,
    )
    assert eff["messaging_permissions"]["telegram"] == {"allowed": True, "mode": "reply"}
    # one level denies -> denied
    eff = combine(
        {"messaging_permissions": {"whatsapp": {"allowed": True, "mode": "full"}}},
        {"messaging_permissions": {"whatsapp": {"allowed": False}}},
        {},
        CATALOG,
    )
    assert eff["messaging_permissions"]["whatsapp"] == {"allowed": False}


def test_invariables_always_floor():
    """require_mfa=false at every level -> effective still true (REQ-POL-004)."""
    eff = combine({"require_mfa": False}, {"require_mfa": False},
                  {"require_mfa": False}, CATALOG)
    assert eff["require_mfa"] is True
    assert eff["approved_models_only"] is True


def test_defaults_when_no_level_defines():
    eff = combine({}, {}, {}, CATALOG)
    assert eff["max_daily_sessions"] == 10
    assert eff["shared_agent_grants_mode"] == "explicit"


# --- endpoint -----------------------------------------------------------------

def test_effective_policy_endpoint(client, idp):
    """Carol (dept A1, user-level 30'): org∩dept connectors, user timeout,
    org require_mfa=true (seed spec 015: existing tenants keep their row)."""
    tokens = mint(client, idp, "carol")
    resp = client.get("/api/me/policies", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    eff = resp.json()
    assert eff["allowed_connectors"] == ["vmware"]  # [vmware,email] ∩ [vmware]
    assert eff["session_timeout_minutes"] == 30
    assert eff["require_mfa"] is True  # org row (D-AUTH-07)
    assert eff["approved_models_only"] is True


def test_dave_sees_defaults(client, idp):
    from tests.conftest import TENANT_B
    tokens = mint(client, idp, "dave", tenant=TENANT_B)
    resp = client.get("/api/me/policies", headers=bearer_headers(tokens))
    assert resp.status_code == 200
    eff = resp.json()
    assert eff["allowed_connectors"] == []
    # Spec 015 (D-AUTH-07): tenant-b has NO org row → catalog default false
    # (how NEW tenants are born: MFA optional, the app suggests enrolling).
    assert eff["require_mfa"] is False
