"""Migración 0047 — ``policy_version`` sube con CUALQUIER cambio de contenido.

Incidente (pilot e2e-lab, 2026-11-10): el admin activó la terminal en Policy
Studio y aprobó un modelo en Model Access; el dispositivo recibió el contenido
nuevo pero con ``policy_version`` 1 de siempre. El agente le dijo al usuario
"versión 1, no hay rastro de un cambio", el heartbeat nunca anunció
``policy_update_available`` y la attestation no probaba qué había aplicado el
dispositivo.

Todo por la API real: el bundle que baja el dispositivo
(``GET /api/me/governance-bundle``), las escrituras de la consola
(``POST/DELETE /api/policy-bundles``, ``PUT /api/models/access``,
``POST /api/models/access/revoke``) y el heartbeat del plugin
(``POST /api/me/device-heartbeat``).
"""
from __future__ import annotations

import jwt as pyjwt

from conftest import (
    DEPT_A1,
    TENANT_A,
    TENANT_B,
    TEST_DB,
    USER_ALICE,
    _admin,
    bearer_headers,
    mint,
)

MODEL = "glm-5.2"  # aprobado a nivel org en el seed: el efectivo no cambia


def _bundle_version(client, idp) -> int:
    from test_governance_bundle import _get_bundle

    got = _get_bundle(client, idp)
    assert got.status_code == 200, got.text
    claims = pyjwt.decode(got.json()["bundle"], options={"verify_signature": False})
    return int(claims["policy_version"])


def _revision(tenant_id: str) -> int:
    with _admin(TEST_DB) as conn:
        row = conn.execute(
            "SELECT revision FROM policy_revisions WHERE tenant_id = %s",
            (tenant_id,),
        ).fetchone()
    return int(row[0]) if row else 0


def _user_level_row(user_id: str):
    with _admin(TEST_DB) as conn:
        return conn.execute(
            "SELECT id, status, mode FROM model_access WHERE tenant_id = %s "
            "AND level = 'user' AND user_id = %s AND model_id = "
            "(SELECT id FROM catalog.model_catalog WHERE model_name = %s)",
            (TENANT_A, user_id, MODEL),
        ).fetchone()


def test_studio_and_model_access_changes_bump_the_version(client, idp):
    from test_live_policy_presence import _heartbeat

    admin = bearer_headers(mint(client, idp, "alice"))
    before_row = _user_level_row(USER_ALICE)
    v0 = _bundle_version(client, idp)
    b0 = _revision(TENANT_B)
    bundle_id = None
    try:
        # Policy Studio: una política personal nueva.
        resp = client.post(
            "/api/policy-bundles",
            json={"name": "revision-check", "desktop": {"cli": True},
                  "user_id": USER_ALICE},
            headers=admin,
        )
        assert resp.status_code == 201, resp.text
        bundle_id = resp.json()["id"]
        v1 = _bundle_version(client, idp)
        assert v1 > v0, "Policy Studio cambió el contenido y la versión no subió"

        # El dispositivo que aún aplica v0 se entera en su heartbeat: pull
        # inmediato (D-027-03) en vez de esperar al próximo ciclo.
        hb = _heartbeat(client, applied=v0)
        assert hb.status_code == 200, hb.text
        assert hb.json()["current_policy_version"] == v1
        assert hb.json()["policy_update_available"] is True

        # Model Access: aprobación a nivel usuario…
        resp = client.put(
            "/api/models/access",
            json={"model_name": MODEL, "level": "user", "user_id": USER_ALICE},
            headers=admin,
        )
        assert resp.status_code == 201, resp.text
        v2 = _bundle_version(client, idp)
        assert v2 > v1, "Model Access aprobó un modelo y la versión no subió"

        # …y revocación: también es contenido nuevo (la versión nunca baja).
        resp = client.post(
            "/api/models/access/revoke",
            json={"model_name": MODEL, "level": "user", "user_id": USER_ALICE},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        v3 = _bundle_version(client, idp)
        assert v3 > v2, "Model Access revocó un modelo y la versión no subió"

        # Borrar la política de Studio también versiona.
        assert client.delete(
            f"/api/policy-bundles/{bundle_id}", headers=admin
        ).status_code in (200, 204)
        bundle_id = None
        v4 = _bundle_version(client, idp)
        assert v4 > v3

        # Un dispositivo al día no recibe falsos avisos de actualización.
        hb = _heartbeat(client, applied=v4)
        assert hb.json()["policy_update_available"] is False

        # Aislamiento: nada de esto versiona a otro tenant.
        assert _revision(TENANT_B) == b0
    finally:
        if bundle_id:
            client.delete(f"/api/policy-bundles/{bundle_id}", headers=admin)
        # Restaurar EXACTAMENTE la fila de nivel usuario previa (o su ausencia):
        # la revocación de arriba no debe cambiar el efectivo de otros tests.
        with _admin(TEST_DB) as conn:
            if before_row is None:
                conn.execute(
                    "DELETE FROM model_access WHERE tenant_id = %s AND level = 'user' "
                    "AND user_id = %s AND model_id = "
                    "(SELECT id FROM catalog.model_catalog WHERE model_name = %s)",
                    (TENANT_A, USER_ALICE, MODEL),
                )
            else:
                conn.execute(
                    "UPDATE model_access SET status = %s, mode = %s WHERE id = %s",
                    (before_row[1], before_row[2], before_row[0]),
                )


def test_any_write_path_bumps_including_manual_sql():
    """La revisión vive en un trigger, no en la API: también cuenta una
    aprobación hecha a mano en psql (como la de glm-5.3 en el pilot)."""
    with _admin(TEST_DB) as conn:
        # Scope que nadie más usa: el seed (y otras suites) dejan filas
        # org/dept/user de glm-5.2 y deepseek, y el unique index
        # model_access_scope_uidx no permite duplicar un scope. Se toma el
        # primer modelo del catálogo SIN ninguna fila en el tenant, y la fila
        # se crea 'denied': el trigger versiona el write, pero los modelos
        # efectivos no cambian (no había aprobación que negar).
        model = conn.execute(
            "SELECT mc.model_name FROM catalog.model_catalog mc "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM model_access ma"
            "  WHERE ma.tenant_id = %s AND ma.model_id = mc.id) "
            "ORDER BY mc.model_name LIMIT 1",
            (TENANT_A,),
        ).fetchone()
        assert model, "ningún modelo del catálogo está libre en el tenant A"
        r0 = _revision(TENANT_A)
        row = conn.execute(
            "INSERT INTO model_access (tenant_id, model_id, level, "
            "department_id, mode, status) VALUES "
            "(%s, (SELECT id FROM catalog.model_catalog "
            "WHERE model_name = %s), 'dept', %s, 'central', 'denied') "
            "RETURNING id",
            (TENANT_A, model[0], DEPT_A1),
        ).fetchone()
    try:
        assert _revision(TENANT_A) == r0 + 1
    finally:
        with _admin(TEST_DB) as conn:
            conn.execute("DELETE FROM model_access WHERE id = %s", (row[0],))
    assert _revision(TENANT_A) == r0 + 2  # el borrado también versiona
