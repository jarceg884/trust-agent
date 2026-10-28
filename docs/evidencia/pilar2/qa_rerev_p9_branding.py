#!/usr/bin/env python3
"""Re-verificación QA independiente (t_1c65ff67) — punto 9 Branding.

Contrato spec 028 (draft→publish, REQ-BRD-004) contra el stack vivo:
  PUT /api/branding        edita el DRAFT (published NO cambia)
  POST /api/branding/publish  publica atómico vN+1 (audit branding.published)
  GET  /api/branding/versions  historial append-only
  POST /api/branding/assets    upload PNG (magic bytes, RLS)
  GET  /api/branding/assets/{id}  sirve los bytes exactos (ETag sha256)
  POST /api/branding/rollback  re-publica como versión NUEVA

Nada simulado: API :8000, Keycloak :8080 y PostgreSQL reales; tenant fresco
provisionado en caliente; login REAL PKCE S256.

Se ejecuta DENTRO del contenedor api (docker exec) porque el host no tiene
psycopg; SCRIPTS apunta a /opt/trust-agent/platform/scripts (bind mount).
"""
import base64
import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

SCRIPTS = Path("/opt/trust-agent/platform/scripts")
_spec = importlib.util.spec_from_file_location(
    "revalidate", SCRIPTS / "revalidate_litellm_real.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

STAMP = time.strftime("%H%M%S")
SLUG = f"qa-p2r9-{STAMP}"
rv.SLUG = SLUG
ADMIN_EMAIL = f"admin@{SLUG}.test"
ADMIN_PW = "QaP2r9Admin2026!"
NAME_A = "QA P2R9 Corp"
NAME_B = "QA P2R9 Corp v2"
PASSES = 0
FAILURES = []

# PNG 1x1 real (magic bytes \x89PNG) para el upload de asset
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, ok, detail=""):
    global PASSES
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  · {detail}" if detail else ""), flush=True)
    if ok:
        PASSES += 1
    else:
        FAILURES.append(name)
    return ok


def multipart(kind, filename, content, ctype):
    b = uuid.uuid4().hex
    body = (
        f"--{b}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n"
        f"{kind}\r\n"
        f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n"
    ).encode() + content + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def http_raw(method, path, body=None, headers=None):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=30)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    raw = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, raw, hdrs


def main():
    print(f"=== QA P2R9 Branding draft→publish (tenant {SLUG}) ===", flush=True)

    # ---------- Provisioning + login REAL ----------
    st, body, raw = rv.api("POST", "/api/admin/tenants", headers={
        "Authorization": f"Bearer {rv.PROVIDER_TOKEN}"}, payload={
        "organization_name": "QA P2R9 Branding Corp",
        "admin_email": ADMIN_EMAIL,
        "initial_budget_usd": 100.0,
        "model_plan": ["glm-5.2"],
        "slug": SLUG})
    check("B0a POST /api/admin/tenants → 201", st == 201,
          f"HTTP {st} {raw[:120]}")
    tenant_id = body.get("tenant_id", body.get("tenant", {}).get("id", ""))
    ota = body["one_time_activation"]["one_time_password"]
    id_token, acr = rv.pkce_login(ADMIN_EMAIL, ota, new_password=ADMIN_PW)
    check("B0b login REAL admin (PKCE S256)", id_token is not None,
          f"acr={acr}")
    admin = rv.mint(id_token, "desktop")["session_token"]

    # ---------- B1: estado inicial publicado ----------
    st, body, raw = rv.api("GET", "/api/branding", bearer=admin)
    check("B1a GET /api/branding → 200", st == 200, f"HTTP {st}")
    v0 = body.get("version")
    name0 = body.get("product_name")
    src0 = body.get("source")
    check("B1b published inicial con version/product_name/source",
          isinstance(v0, int) and bool(name0) and bool(src0),
          f"version={v0} product_name={name0!r} source={src0!r}")

    # ---------- B2: PUT edita el DRAFT, NO el published ----------
    st, body, raw = rv.api("PUT", "/api/branding", bearer=admin, payload={
        "product_name": NAME_A, "accent_color": "#0F766E"})
    check("B2a PUT /api/branding → 200 (respuesta {draft, published})",
          st == 200 and "draft" in body and "published" in body,
          f"HTTP {st} keys={list(body)[:6]}")
    check("B2b draft.product_name == nuevo", 
          body.get("draft", {}).get("product_name") == NAME_A,
          f"draft={body.get('draft', {}).get('product_name')!r}")
    check("B2c published.product_name SIGUE siendo el anterior (REQ-BRD-004)",
          body.get("published", {}).get("product_name") == name0,
          f"published={body.get('published', {}).get('product_name')!r}")
    check("B2d draft.has_changes == True",
          body.get("draft", {}).get("has_changes") is True)

    # ---------- B3: DB + audit del draft ----------
    rows = rv.db(
        "SELECT branding->>'product_name', branding_draft->>'product_name' "
        "FROM tenants WHERE id=%s", (tenant_id,))
    check("B3a DB: tenants.branding (published) SIN cambiar, "
          "branding_draft CON el cambio",
          rows and rows[0][0] == name0 and rows[0][1] == NAME_A,
          f"published={rows[0][0]!r} draft={rows[0][1]!r}" if rows else "∅")
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE tenant_id=%s "
        "AND action='branding.draft.updated'", (tenant_id,))
    check("B3b audit branding.draft.updated ≥ 1 en DB",
          rows and rows[0][0] >= 1, f"count={rows[0][0] if rows else 0}")

    # ---------- B4: published visible NO cambia hasta publish ----------
    st, body, raw = rv.api("GET", "/api/branding", bearer=admin)
    check("B4 GET /api/branding (consumidor) sigue mostrando el anterior "
          "(visibilidad solo tras publish)",
          st == 200 and body.get("product_name") == name0,
          f"HTTP {st} product_name={body.get('product_name')!r}")

    # ---------- B5: publish atómico v1 ----------
    st, body, raw = rv.api("POST", "/api/branding/publish", bearer=admin)
    check("B5a POST /api/branding/publish → 200, version == v0+1",
          st == 200 and body.get("version") == v0 + 1,
          f"HTTP {st} version={body.get('version')} (v0={v0})")
    check("B5b published tras publish == nuevo",
          body.get("published", {}).get("product_name") == NAME_A,
          f"published={body.get('published', {}).get('product_name')!r}")
    rows = rv.db(
        "SELECT branding->>'product_name' FROM tenants WHERE id=%s",
        (tenant_id,))
    check("B5c DB tenants.branding ahora == nuevo",
          rows and rows[0][0] == NAME_A, f"{rows[0][0]!r}" if rows else "∅")
    rows = rv.db(
        "SELECT count(*) FROM audit_log WHERE tenant_id=%s "
        "AND action='branding.published'", (tenant_id,))
    check("B5d audit branding.published ≥ 1",
          rows and rows[0][0] >= 1, f"count={rows[0][0] if rows else 0}")

    # ---------- B6: segunda edición + publish v2 ----------
    st, body, raw = rv.api("PUT", "/api/branding", bearer=admin,
                           payload={"product_name": NAME_B})
    check("B6a PUT draft v2 → 200", st == 200, f"HTTP {st}")
    st, body, raw = rv.api("POST", "/api/branding/publish", bearer=admin)
    check("B6b publish v2 → version == v0+2",
          st == 200 and body.get("version") == v0 + 2,
          f"HTTP {st} version={body.get('version')}")

    # ---------- B7: historial append-only ----------
    st, body, raw = rv.api("GET", "/api/branding/versions", bearer=admin)
    vers = [v.get("version") for v in body.get("versions", [])]
    check("B7 GET /api/branding/versions → historial append-only "
          "{v0+1, v0+2} y current == v0+2",
          st == 200 and sorted(vers) == [v0 + 1, v0 + 2]
          and body.get("current_version") == v0 + 2,
          f"HTTP {st} versions={vers} current={body.get('current_version')}")

    # ---------- B8: asset upload + serving ----------
    sha = hashlib.sha256(PNG_1PX).hexdigest()
    mp, ct = multipart("logo_light", "logo.png", PNG_1PX, "image/png")
    st, raw, hdrs = http_raw("POST", "/api/branding/assets", body=mp,
                             headers={"Content-Type": ct,
                                      "Authorization": f"Bearer {admin}"})
    up = json.loads(raw) if raw else {}
    asset_id = up.get("asset", {}).get("asset_id", "")
    check("B8a POST /api/branding/assets (multipart PNG) → 200 con asset_id",
          st == 200 and bool(asset_id), f"HTTP {st} asset_id={asset_id!r}")
    check("B8b sha256 reportado == sha256 local",
          up.get("asset", {}).get("sha256") == sha,
          f"{up.get('asset', {}).get('sha256')} vs {sha}")
    st2, raw2, hdrs2 = http_raw(
        "GET", f"/api/branding/assets/{asset_id}",
        headers={"Authorization": f"Bearer {admin}"})
    lower = {k.lower(): v for k, v in hdrs2.items()}
    check("B8c GET /api/branding/assets/{id} → 200 bytes EXACTOS",
          st2 == 200 and raw2 == PNG_1PX,
          f"HTTP {st2} bytes={len(raw2)} (esperados {len(PNG_1PX)})")
    check("B8d ETag == sha256 y Content-Type image/png",
          lower.get("etag", "").strip('"') == sha
          and "image/png" in lower.get("content-type", ""),
          f"etag={lower.get('etag')!r} ct={lower.get('content-type')!r}")
    st, body, raw = rv.api("GET", "/api/branding/draft", bearer=admin)
    check("B8e draft referencia el asset logo_light",
          st == 200 and (body.get("logo_light") or {}).get("asset_id")
          == asset_id,
          f"logo_light={body.get('logo_light')}")

    # ---------- B9: rollback re-publica como versión NUEVA ----------
    st, body, raw = rv.api("POST", "/api/branding/rollback", bearer=admin,
                           payload={"version": v0 + 1})
    check("B9a POST /api/branding/rollback {version: v0+1} → 200, "
          "version NUEVA > v0+2 (nunca destruye historial)",
          st == 200 and body.get("version", 0) > v0 + 2,
          f"HTTP {st} version={body.get('version')}")
    check("B9b published tras rollback == contenido de v0+1",
          body.get("published", {}).get("product_name") == NAME_A,
          f"published={body.get('published', {}).get('product_name')!r}")
    st, body, raw = rv.api("GET", "/api/branding/versions", bearer=admin)
    vers = [v.get("version") for v in body.get("versions", [])]
    check("B9c historial sigue append-only: v0+1 y v0+2 preservadas",
          st == 200 and v0 + 1 in vers and v0 + 2 in vers,
          f"versions={vers}")

    # ---------- B10: audit completo ----------
    rows = rv.db(
        "SELECT action, count(*) FROM audit_log WHERE tenant_id=%s "
        "AND action LIKE %s GROUP BY action ORDER BY action",
        (tenant_id, "branding.%"))
    acts = {r[0]: r[1] for r in rows}
    check("B10 audit branding.* completo en DB",
          acts.get("branding.draft.updated", 0) >= 1
          and acts.get("branding.published", 0) >= 2
          and acts.get("branding.rolled_back", 0) >= 1,
          f"actions={acts}")

    print(f"\n=== RESULTADO: {PASSES} PASS, {len(FAILURES)} FAIL ===",
          flush=True)
    if FAILURES:
        print("FALLOS:", FAILURES, flush=True)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
