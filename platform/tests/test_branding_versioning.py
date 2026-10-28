"""Spec 028 — Branding REQ-BRD-004: draft/publish versionado, uploads de
assets (logo light/dark + favicon) y rollback.

Cubre AC-028-1 (draft invisible hasta publish), AC-028-2 (validación de
uploads), AC-028-3 (publish atómico + audit con versión), AC-028-4
(rollback) y AC-028-5 (aislamiento multi-tenant del servicio de assets).
"""
import os
import uuid

import pytest

from conftest import TENANT_A, TENANT_B, _admin, bearer_headers, mint

TEST_DB = os.environ.get("TRUST_TEST_DB", "trust_agent_platform_test")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"payload-not-decoded"
SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">'
       b'<rect width="24" height="24" rx="6" fill="#c90104"/></svg>')
SVG_SCRIPT = (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
              b'<script>alert(1)</script></svg>')


def _audit_count(action, tenant=TENANT_A):
    with _admin(TEST_DB) as c:
        row = c.execute(
            "SELECT count(*) FROM audit_log "
            f"WHERE tenant_id = '{tenant}' AND action = '{action}'"
        ).fetchone()
    return row[0]


@pytest.fixture(autouse=True)
def _pristine_tenant_a():
    """Cada test arranca y termina con el tenant A sin branding (la suite
    comparte DB con otros módulos)."""
    def reset():
        with _admin(TEST_DB) as c:
            c.execute(f"DELETE FROM branding_versions WHERE tenant_id = '{TENANT_A}'")
            c.execute(f"DELETE FROM branding_assets WHERE tenant_id = '{TENANT_A}'")
            c.execute(
                f"UPDATE tenants SET branding = '{{}}'::jsonb, "
                f"branding_draft = '{{}}'::jsonb, branding_version = 0 "
                f"WHERE id = '{TENANT_A}'"
            )
    reset()
    yield
    reset()


def _put_draft(client, alice, body):
    return client.put("/api/branding", json=body, headers=bearer_headers(alice))


def _publish(client, alice):
    return client.post("/api/branding/publish", headers=bearer_headers(alice))


def _upload(client, alice, kind=None, content=None, filename="logo.png"):
    return client.post(
        "/api/branding/assets",
        data={"kind": "logo_light" if kind is None else kind},
        files={"file": (filename, SVG if content is None else content,
                        "application/octet-stream")},
        headers=bearer_headers(alice),
    )


# --- AC-028-1: draft invisible hasta publish (REQ-BRD-004) --------------------

def test_draft_is_invisible_until_published(client, idp):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    resp = _put_draft(client, alice, {"product_name": "Acme Suite",
                                      "accent_color": "#0F766E"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft"]["has_changes"] is True

    # Otro usuario NO ve el cambio (REQ-BRD-004).
    published = client.get("/api/branding", headers=bearer_headers(carol)).json()
    assert published["source"] == "provider"
    assert published["product_name"] == "Trust Agent"
    assert published["version"] == 0

    # Publish atómico → visible + audit branding.published con versión.
    before = _audit_count("branding.published")
    resp = _publish(client, alice)
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 1
    assert _audit_count("branding.published") == before + 1

    published = client.get("/api/branding", headers=bearer_headers(carol)).json()
    assert published["source"] == "tenant"
    assert published["product_name"] == "Acme Suite"
    assert published["accent_color"] == "#0F766E"
    assert published["version"] == 1

    # El draft queda sincronizado: sin cambios pendientes.
    draft = client.get("/api/branding/draft",
                       headers=bearer_headers(alice)).json()
    assert draft["has_changes"] is False
    assert draft["version"] == 1


def test_publish_rejects_empty_draft_and_no_changes(client, idp):
    alice = mint(client, idp, "alice")
    # Draft vacío → 422.
    assert _publish(client, alice).status_code == 422
    _put_draft(client, alice, {"product_name": "One"})
    assert _publish(client, alice).status_code == 200
    # Sin cambios nuevos → 422 (sin spam de versiones).
    assert _publish(client, alice).status_code == 422


# --- AC-028-2: validación de uploads -------------------------------------------

def test_asset_upload_png_and_svg(client, idp):
    alice = mint(client, idp, "alice")
    uploads_before = _audit_count("branding.asset.uploaded")

    resp = _upload(client, alice, content=PNG, filename="logo.png")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["asset"]["mime"] == "image/png"  # mime DERIVADO, no del client
    assert body["asset"]["kind"] == "logo_light"
    assert body["draft"]["logo_light"]["asset_id"] == body["asset"]["asset_id"]
    assert body["draft"]["has_changes"] is True
    assert _audit_count("branding.asset.uploaded") == uploads_before + 1

    resp = _upload(client, alice, kind="logo_dark", content=SVG,
                   filename="logo-dark.svg")
    assert resp.status_code == 200
    assert resp.json()["asset"]["mime"] == "image/svg+xml"

    # El asset del draft se publica con el branding.
    resp = _publish(client, alice)
    assert resp.status_code == 200
    published = resp.json()["published"]
    assert published["logo_light_url"] == \
        f"/api/branding/assets/{body['asset']['asset_id']}"
    assert published["logo_dark_url"] is not None
    # Alias retrocompatible (D-028-06): logo_url = asset light.
    assert published["logo_url"] == published["logo_light_url"]


def test_asset_upload_validation_rejections(client, idp):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    before = _audit_count("branding.asset.uploaded")

    # Sin branding.manage → 403.
    assert _upload(client, carol).status_code == 403
    # Kind inválido → 422.
    assert _upload(client, alice, kind="watermark").status_code == 422
    # Contenido no PNG/SVG (magic bytes, no content-type del client) → 422.
    assert _upload(client, alice, content=b"GIF89a not allowed",
                   filename="logo.gif").status_code == 422
    # SVG con contenido activo (script/onload) → 422.
    assert _upload(client, alice, content=SVG_SCRIPT,
                   filename="evil.svg").status_code == 422
    # > 2 MB → 422.
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024 + 1)
    assert _upload(client, alice, content=big, filename="big.png").status_code == 422
    # Archivo vacío → 422.
    assert _upload(client, alice, content=b"",
                   filename="empty.png").status_code == 422
    # Nada quedó auditado ni en el draft.
    assert _audit_count("branding.asset.uploaded") == before


# --- AC-028-5: servicio de assets + aislamiento ---------------------------------

def test_asset_serving_and_tenant_isolation(client, idp):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    dave = mint(client, idp, "dave", tenant=TENANT_B)

    asset_id = _upload(client, alice, content=PNG).json()["asset"]["asset_id"]

    # Mismo tenant: bytes exactos + headers de seguridad.
    resp = client.get(f"/api/branding/assets/{asset_id}",
                      headers=bearer_headers(carol))
    assert resp.status_code == 200
    assert resp.content == PNG
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["etag"]

    # Otro tenant: 404 (RLS — el asset no existe en su scope).
    assert client.get(f"/api/branding/assets/{asset_id}",
                      headers=bearer_headers(dave)).status_code == 404
    # UUID inválido → 422, no 500.
    assert client.get("/api/branding/assets/not-a-uuid",
                      headers=bearer_headers(carol)).status_code == 422
    # UUID válido pero inexistente → 404.
    assert client.get(f"/api/branding/assets/{uuid.uuid4()}",
                      headers=bearer_headers(carol)).status_code == 404


# --- AC-028-3/4: versiones, historial y rollback ---------------------------------

def test_versions_listing_and_rollback(client, idp):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")

    _put_draft(client, alice, {"product_name": "Version One"})
    assert _publish(client, alice).json()["version"] == 1
    _put_draft(client, alice, {"product_name": "Version Two"})
    assert _publish(client, alice).json()["version"] == 2
    assert client.get("/api/branding",
                      headers=bearer_headers(carol)).json()["product_name"] \
        == "Version Two"

    # Historial: 2 versiones, la más reciente primero.
    resp = client.get("/api/branding/versions", headers=bearer_headers(alice))
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_version"] == 2
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert body["versions"][1]["summary"]["product_name"] == "Version One"

    # Rollback a la v1 → nueva versión 3 con el data exacto de v1.
    before = _audit_count("branding.rolled_back")
    resp = client.post("/api/branding/rollback", json={"version": 1},
                       headers=bearer_headers(alice))
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == 3
    assert resp.json()["restored_from"] == 1
    assert _audit_count("branding.rolled_back") == before + 1

    published = client.get("/api/branding",
                           headers=bearer_headers(carol)).json()
    assert published["product_name"] == "Version One"
    assert published["version"] == 3

    # Rollback a la versión YA activa → 422; a una inexistente → 404.
    assert client.post("/api/branding/rollback", json={"version": 3},
                       headers=bearer_headers(alice)).status_code == 422
    assert client.post("/api/branding/rollback", json={"version": 99},
                       headers=bearer_headers(alice)).status_code == 404


# --- Permisos: todo el flujo exige branding.manage ------------------------------

def test_draft_endpoints_require_manage(client, idp):
    alice = mint(client, idp, "alice")
    carol = mint(client, idp, "carol")
    _put_draft(client, alice, {"product_name": "Managed"})

    assert client.get("/api/branding/draft",
                      headers=bearer_headers(carol)).status_code == 403
    assert client.get("/api/branding/versions",
                      headers=bearer_headers(carol)).status_code == 403
    assert _publish(client, carol).status_code == 403
    assert client.post("/api/branding/rollback", json={"version": 1},
                       headers=bearer_headers(carol)).status_code == 403
    # GET publicado y assets siguen ABIERTOS a cualquier principal.
    assert client.get("/api/branding",
                      headers=bearer_headers(carol)).status_code == 200
