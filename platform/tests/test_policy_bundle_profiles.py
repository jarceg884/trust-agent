"""Spec 044 — perfiles de la política: validación, roundtrip y bundle.

Incidente (pilot e2e-lab, 2026-11-10): el admin activó el switch Profiles y el
escritorio mostró el rail, pero la política tenía la lista de perfiles VACÍA —
el editor de la consola mandaba ``profiles: []`` en cada PUT y el servidor
escribe lo que recibe, así que editar la política borraba los perfiles. El
empleado además podía tocar perfiles locales que el gateway no sirve
(``profile_mismatch``).

Contrato probado aquí (la mitad servidor):
  1. un perfil es ``{name, model}`` con nombre y modelo, sin duplicados;
  2. el modelo del perfil debe estar entre los ``models`` de la política
     (un perfil no puede abrir la puerta a un modelo no concedido);
  3. create y update persisten la lista tal cual (el editor la carga y la
     manda; el servidor ya no recibe ``[]`` por accidente);
  4. el bundle firmado que baja el dispositivo lleva ``policy_profiles``.
"""
import jwt as pyjwt

from conftest import DEPT_A1, USER_ALICE, bearer_headers, mint

PROFILES = [{"name": "Operaciones", "model": "glm-5.2"}]


def _admin(client, idp):
    return bearer_headers(mint(client, idp, "alice"))


def _body(**overrides):
    body = {
        "name": "perfiles-check",
        "description": "",
        "desktop": {"profiles": True},
        "credentials": {},
        "global_prompt": "",
        "profiles": PROFILES,
        "contracts": [],
        "models": ["glm-5.2"],
        "default_model": "glm-5.2",
        "agents": [],
        "department_id": DEPT_A1,
        "user_id": None,
    }
    body.update(overrides)
    return body


def test_profiles_roundtrip_and_update_keeps_them(client, idp):
    admin = _admin(client, idp)
    created = client.post("/api/policy-bundles", json=_body(), headers=admin)
    assert created.status_code == 201, created.text
    bundle_id = created.json()["id"]
    try:
        assert created.json()["profiles"] == PROFILES

        # PUT con la lista en el body: persiste (regresión del wipe — el
        # editor de la consola antes mandaba [] y borraba los perfiles).
        updated = client.put(
            f"/api/policy-bundles/{bundle_id}",
            json=_body(name="perfiles-check-2"),
            headers=admin,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["profiles"] == PROFILES
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)


def test_profile_model_must_be_in_the_policy_models(client, idp):
    admin = _admin(client, idp)
    # El perfil nombra un modelo que la política NO concede.
    resp = client.post(
        "/api/policy-bundles",
        json=_body(profiles=[{"name": "Hueco", "model": "glm-5.3"}]),
        headers=admin,
    )
    assert resp.status_code == 422, resp.text
    assert "no está entre los modelos" in resp.json()["error"]["message"]

    # Lo mismo en UPDATE: la validación no se salta por la vía de edición.
    created = client.post("/api/policy-bundles", json=_body(), headers=admin)
    assert created.status_code == 201, created.text
    bundle_id = created.json()["id"]
    try:
        resp = client.put(
            f"/api/policy-bundles/{bundle_id}",
            json=_body(profiles=[{"name": "Hueco", "model": "glm-5.3"}]),
            headers=admin,
        )
        assert resp.status_code == 422, resp.text
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)


def test_profile_entries_validated(client, idp):
    admin = _admin(client, idp)
    for bad, why in (
        ([{"name": "", "model": "glm-5.2"}], "sin nombre"),
        ([{"name": "X"}], "sin modelo"),
        (["no-dict"], "no es objeto"),
        ([{"name": "A", "model": "glm-5.2"}, {"name": "a", "model": "glm-5.2"}], "duplicado"),
    ):
        resp = client.post("/api/policy-bundles", json=_body(profiles=bad), headers=admin)
        assert resp.status_code == 422, f"{why}: {resp.text}"


def test_bundle_ships_policy_profiles(client, idp):
    """El dispositivo recibe la lista en el bundle firmado (``policy_profiles``):
    es lo que el escritorio gobernado ofrece — nada de perfiles locales."""
    from test_governance_bundle import _get_bundle

    admin = _admin(client, idp)
    created = client.post("/api/policy-bundles", json=_body(), headers=admin)
    assert created.status_code == 201, created.text
    bundle_id = created.json()["id"]
    try:
        got = _get_bundle(client, idp)
        assert got.status_code == 200, got.text
        claims = pyjwt.decode(got.json()["bundle"], options={"verify_signature": False})
        assert claims["policy_profiles"] == PROFILES
        # El usuario de la política es quien la hereda; alice (org_admin del
        # mismo depto) la ve compilada.
        assert claims["policy_version"] >= 1
    finally:
        client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)
