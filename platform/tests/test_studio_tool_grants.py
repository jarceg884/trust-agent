"""Policy Studio → CLI & Agente: los switches CONCEDEN y RETIRAN tools.

Incidente 2026-11-10 (e2e-lab): el admin activó "Terminal / shell"; el
dispositivo recibió el switch en el bundle firmado, pero ``allowed_tools``
siguió en ``["governance_status"]``. El compilador solo RESTABA familias
sobre el default del proveedor (``catalog.policy_keys.allowed_tools``, sin
terminal), así que el switch en ON no podía conceder nada.

Contrato (``app.governance.bundle.clamp_tools_to_studio``):
  * ON concede la familia (nombres reales de hermes-agent ``toolsets.py``);
  * OFF la retira (incluye alias legados);
  * un ``allowed_tools`` explícito (org/depto/usuario) es techo: ON no lo
    excede (deny-overrides);
  * ``cli`` OFF → ninguna tool.
"""
from __future__ import annotations

import json

import jwt as pyjwt
import pytest

from conftest import TENANT_A, USER_ALICE, bearer_headers, mint

from app.governance.bundle import STUDIO_TOOL_FAMILIES, clamp_tools_to_studio

# Default del proveedor (catalog.policy_keys.allowed_tools): sin terminal.
PROVIDER_DEFAULT = ["web_search", "read_file", "write_file", "governance_status"]

# Mapa ``desktop`` EXACTO que guardó el admin de e2e-lab (2026-11-10).
INCIDENT_UI = {
    "cli": True, "cron": False, "keys": False, "vault": False, "voice": False,
    "agents": True, "memory": True, "billing": False, "browser": False,
    "cli_web": False, "plugins": False, "cli_code": False, "gateways": False,
    "profiles": False, "sessions": True, "terminal": False, "artifacts": False,
    "messaging": False, "model_moa": False, "providers": False,
    "workspace": True, "model_main": True, "web_access": False,
    "capabilities": False, "cli_terminal": True, "cli_files_read": False,
    "model_fallback": False, "cli_files_write": False,
    "chat_model_switch": True,
}


# =============================================================================
# Función pura
# =============================================================================


def test_incident_policy_grants_terminal():
    tools = clamp_tools_to_studio(PROVIDER_DEFAULT, INCIDENT_UI, explicit_ceiling=False)
    assert tools == ["governance_status", "terminal", "process_manage"]


@pytest.mark.parametrize("feature", sorted(STUDIO_TOOL_FAMILIES))
def test_each_family_grants_its_real_hermes_tools(feature):
    tools = clamp_tools_to_studio(
        ["governance_status"], {feature: True}, explicit_ceiling=False
    )
    assert tools == ["governance_status", *STUDIO_TOOL_FAMILIES[feature]["grant"]]


def test_off_removes_family_including_legacy_aliases():
    base = ["terminal", "shell", "process_manage", "read_file", "governance_status"]
    tools = clamp_tools_to_studio(base, {"cli_terminal": False}, explicit_ceiling=False)
    assert tools == ["read_file", "governance_status"]


def test_explicit_policy_is_a_ceiling():
    explicit = ["web_search", "governance_status"]
    ui = {"cli": True, "cli_terminal": True, "cli_web": False}
    # ON no excede el techo explícito; OFF sigue retirando.
    assert clamp_tools_to_studio(explicit, ui, explicit_ceiling=True) == ["governance_status"]


def test_cli_master_switch_off_means_no_tools():
    ui = {"cli": False, "cli_terminal": True}
    assert clamp_tools_to_studio(PROVIDER_DEFAULT, ui, explicit_ceiling=False) == []


def test_absent_switches_keep_base_deduplicated():
    base = ["web_search", "read_file", "web_search", "governance_status"]
    assert clamp_tools_to_studio(base, {}, explicit_ceiling=False) == [
        "web_search", "read_file", "governance_status",
    ]


def test_grant_is_idempotent():
    tools = clamp_tools_to_studio(
        ["terminal", "governance_status"], {"cli_terminal": True},
        explicit_ceiling=False,
    )
    assert tools == ["terminal", "governance_status", "process_manage"]


# =============================================================================
# Camino real: consola (POST/PUT /api/policy-bundles) → bundle firmado
# =============================================================================


def _payload(resp) -> dict:
    assert resp.status_code == 200, resp.text
    return pyjwt.decode(resp.json()["bundle"], options={"verify_signature": False})


def _get_bundle(client, idp):
    from test_governance_bundle import _get_bundle as get

    return get(client, idp)


def _explicit_tools_rows(admin_conn) -> list:
    return admin_conn.execute(
        "SELECT id FROM policies "
        "WHERE tenant_id = %s AND key = 'allowed_tools' AND is_active",
        (TENANT_A,),
    ).fetchall()


@pytest.fixture()
def alice_studio_policy(client, idp):
    """Política PERSONAL de alice creada por la API de Policy Studio."""
    admin = bearer_headers(mint(client, idp, "alice"))
    created: list[str] = []

    def body(desktop: dict) -> dict:
        return {"name": "studio-tools-test", "desktop": desktop, "user_id": USER_ALICE}

    def make(desktop: dict) -> str:
        resp = client.post("/api/policy-bundles", json=body(desktop), headers=admin)
        assert resp.status_code == 201, resp.text
        created.append(resp.json()["id"])
        return created[-1]

    def update(bundle_id: str, desktop: dict) -> None:
        resp = client.put(
            f"/api/policy-bundles/{bundle_id}", json=body(desktop), headers=admin
        )
        assert resp.status_code == 200, resp.text

    yield make, update
    for bundle_id in created:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)


def test_studio_terminal_switch_reaches_signed_bundle(
    client, idp, admin_conn, alice_studio_policy
):
    make, update = alice_studio_policy
    assert not _explicit_tools_rows(admin_conn), (
        "precondición: el tenant no fija allowed_tools explícito"
    )

    bundle_id = make(INCIDENT_UI)
    tools = _payload(_get_bundle(client, idp))["allowed_tools"]
    assert "terminal" in tools and "process_manage" in tools, tools
    for denied in ("read_file", "write_file", "web_search", "execute_code"):
        assert denied not in tools, tools

    # El admin lo vuelve a apagar: el siguiente sync lo retira.
    update(bundle_id, {**INCIDENT_UI, "cli_terminal": False})
    tools = _payload(_get_bundle(client, idp))["allowed_tools"]
    assert "terminal" not in tools and "process_manage" not in tools, tools


def test_explicit_allowed_tools_caps_the_studio_grant(
    client, idp, admin_conn, alice_studio_policy
):
    make, _ = alice_studio_policy
    make(INCIDENT_UI)
    dept = admin_conn.execute(
        "SELECT department_id FROM users WHERE id = %s", (USER_ALICE,)
    ).fetchone()[0]
    row = admin_conn.execute(
        "INSERT INTO policies (tenant_id, level, department_id, user_id, key, value, version) "
        "VALUES (%s, 'user', %s, %s, 'allowed_tools', %s::jsonb, 1) RETURNING id",
        (TENANT_A, dept, USER_ALICE, json.dumps(["web_search", "governance_status"])),
    ).fetchone()
    admin_conn.commit()
    try:
        tools = _payload(_get_bundle(client, idp))["allowed_tools"]
        # Techo explícito del admin: el switch ON no lo excede.
        assert "terminal" not in tools and "process_manage" not in tools, tools
        assert "governance_status" in tools, tools
    finally:
        admin_conn.execute("DELETE FROM policies WHERE id = %s", (row[0],))
        admin_conn.commit()
