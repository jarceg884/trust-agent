"""Spec 045 (D-045-04) — perfiles de la política: vienen del marketplace.

Antes (spec 044) un perfil era ``{name, model}``; desde spec 045 la política elige perfiles del
marketplace — ``[{profile_id, model}]`` — y marca ``default_profile_id``. Contrato probado aquí
(la mitad servidor):
  1. cada perfil existe y está activo en el marketplace del tenant, sin duplicados;
  2. el modelo del perfil debe estar entre los ``models`` de la política (regla de spec 044:
     un perfil no puede abrir la puerta a un modelo no concedido);
  3. con perfiles, el default es obligatorio y debe ser uno de ellos;
  4. create y update persisten la lista normalizada y el default (regresión del wipe de
     spec 044: el editor la carga y la manda; el servidor no recibe ``[]`` por accidente);
  5. el bundle firmado que baja el dispositivo lleva ``policy_profiles``.
"""
import uuid

import jwt as pyjwt
import pytest

from conftest import DEPT_A1, bearer_headers, mint


def _admin(client, idp):
    return bearer_headers(mint(client, idp, "alice"))


@pytest.fixture
def factory_ids(client, idp):
    resp = client.get("/api/marketplace/profiles", headers=_admin(client, idp))
    assert resp.status_code == 200, resp.text
    return {p["factory_key"]: p["id"] for p in resp.json()["profiles"] if p["source"] == "factory"}


def _body(profiles, default, **overrides):
    body = {
        "name": f"perfiles-check-{uuid.uuid4().hex[:6]}",
        "description": "",
        "desktop": {"profiles": True},
        "credentials": {},
        "global_prompt": "",
        "profiles": profiles,
        "default_profile_id": default,
        "contracts": [],
        "models": ["glm-5.2", "glm-5.3"],
        "default_model": "glm-5.2",
        "agents": [],
        "department_id": DEPT_A1,
        "user_id": None,
    }
    body.update(overrides)
    return body


def test_profiles_roundtrip_and_update_keeps_them(client, idp, factory_ids):
    admin = _admin(client, idp)
    analyst, coder = factory_ids["analyst"], factory_ids["coder"]
    profiles = [
        {"profile_id": analyst, "model": "glm-5.2", "ignored": "dropped by the server"},
        {"profile_id": coder, "model": "glm-5.3"},
    ]
    created = client.post("/api/policy-bundles", json=_body(profiles, coder), headers=admin)
    assert created.status_code == 201, created.text
    bundle_id = created.json()["id"]
    expected = [{"profile_id": analyst, "model": "glm-5.2"}, {"profile_id": coder, "model": "glm-5.3"}]
    try:
        assert created.json()["profiles"] == expected
        assert created.json()["default_profile_id"] == coder
        updated = client.put(
            f"/api/policy-bundles/{bundle_id}",
            json=_body(profiles, analyst, name=created.json()["name"]),
            headers=admin,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["profiles"] == expected
        assert updated.json()["default_profile_id"] == analyst
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)


def test_profile_model_must_be_in_the_policy_models(client, idp, factory_ids):
    admin = _admin(client, idp)
    ops = factory_ids["ops"]
    resp = client.post(
        "/api/policy-bundles",
        json=_body([{"profile_id": ops, "model": "glm-9"}], ops),
        headers=admin,
    )
    assert resp.status_code == 422, resp.text
    assert "no está entre los modelos" in resp.json()["error"]["message"]


def test_profile_entries_validated(client, idp, factory_ids):
    admin = _admin(client, idp)
    ops, coder = factory_ids["ops"], factory_ids["coder"]
    unknown = str(uuid.uuid4())
    for profiles, default, why in (
        ([{"name": "Operaciones", "model": "glm-5.2"}], None, "forma vieja {name, model}"),
        ([{"profile_id": ops}], ops, "sin modelo"),
        (["no-dict"], None, "no es objeto"),
        ([{"profile_id": ops, "model": "glm-5.2"}, {"profile_id": ops, "model": "glm-5.3"}], ops, "duplicado"),
        ([{"profile_id": unknown, "model": "glm-5.2"}], unknown, "no existe en el marketplace"),
        ([{"profile_id": "not-a-uuid", "model": "glm-5.2"}], None, "uuid inválido"),
        ([{"profile_id": ops, "model": "glm-5.2"}], None, "sin default"),
        ([{"profile_id": ops, "model": "glm-5.2"}], coder, "default fuera de la lista"),
        ([], ops, "default sin perfiles"),
    ):
        resp = client.post("/api/policy-bundles", json=_body(profiles, default), headers=admin)
        assert resp.status_code == 422, f"{why}: {resp.text}"


def test_policy_without_profiles_is_still_valid(client, idp):
    admin = _admin(client, idp)
    created = client.post("/api/policy-bundles", json=_body([], None), headers=admin)
    assert created.status_code == 201, created.text
    try:
        assert created.json()["profiles"] == [] and created.json()["default_profile_id"] is None
    finally:
        client.delete(f"/api/policy-bundles/{created.json()['id']}", headers=admin)


def test_bundle_ships_policy_profiles(client, idp, factory_ids):
    """El dispositivo recibe la lista en el bundle firmado (``policy_profiles``)."""
    from test_governance_bundle import _get_bundle

    admin = _admin(client, idp)
    ops = factory_ids["ops"]
    created = client.post(
        "/api/policy-bundles",
        json=_body([{"profile_id": ops, "model": "glm-5.2"}], ops),
        headers=admin,
    )
    assert created.status_code == 201, created.text
    bundle_id = created.json()["id"]
    try:
        got = _get_bundle(client, idp)
        assert got.status_code == 200, got.text
        claims = pyjwt.decode(got.json()["bundle"], options={"verify_signature": False})
        assert [p["profile_id"] for p in claims["policy_profiles"]] == [ops]
        assert claims["policy_version"] >= 1
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)
