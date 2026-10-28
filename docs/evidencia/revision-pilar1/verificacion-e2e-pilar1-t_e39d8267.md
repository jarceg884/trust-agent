# Verificación de evidencia e2e del Pilar 1 — SaaS proveedor (tarea t_e39d8267)

> Tarea: t_e39d8267 (verificación de la revisión Pilar 1 de la revisión maestra t_ef306f32).
> Fecha: 2026-10-27 (CST) · Perfil: qa · Método: re-verificación de CADA punto del reporte de cierre
> `docs/revision-pilar1-saas-proveedor.md` (t_fb31dc49) contra el estado REAL: stack vivo
> (api :8000, KC :8080, consoles :3100/:3200), PostgreSQL (`docker exec trust-agent-db psql`),
> código en `/root/trust-agent/`, specs y board (SQL directo a
> `/root/.hermes/kanban/boards/trust-agent/kanban.db`). Nada simulado: los comandos abajo
> fueron ejecutados en este run y sus salidas son las citadas.

## Tabla punto a punto

| # | Punto del reporte | Veredicto | Evidencia real (comando → resultado) |
|---|---|---|---|
| 1 | Flujo 1 — Crear tenant desde la UI (provisioning idiom-proof) | **VERIFICADO** | psql: tenant `qa-pilar1-review` `active`, creado 2026-10-26 19:20:49 UTC; conteos exactos del reporte: **4 deptos, 1 user, 1 org budget (25000¢ = $250), 3 policies activas, 6 model_access**. `curl http://127.0.0.1:8080/realms/qa-pilar1-review/.well-known/openid-configuration` → **200** (realm KC real). `GET /api/admin/tenants/qa-pilar1-review` (bearer provider) → 200 con `counts` idénticos. Audit en BD: `tenant.provisioned` + 6× `model.approved` + 3× `policy.updated` + 4× `department.created` + `user.created` + `role.assigned`, todos `actor_role=provider` ts 19:20:50. Nota: el primer login PKCE del admin NO fue re-ejecutado en este run; queda corroborado por `auth.login` (org_admin) ×2 en el audit y el realm vivo. |
| 2 | Flujo 2 — Listar tenants | **VERIFICADO** | `GET /api/admin/tenants` → 200, **78 tenants**, `qa-pilar1-review` presente. (El reporte citaba 48 filas en su momento; el conteo creció por tenants aprovisionados por otros runs de QA posteriores — consistente, no contradicción.) |
| 3 | Flujo 3 — Detalle de tenant (sin bloque download, corrección Miller t_360a752e) | **VERIFICADO** | `GET /api/admin/tenants/qa-pilar1-review` → 200 con issuer, realm, branding, created_at, counts. `grep -rni "download" platform/provider-console/src/app/(provider)/tenants/` → **0 apariciones** (el bloque fue mudado). `/hermes-download` existe en el Admin Console: `platform/console/src/app/(console)/hermes-download/` (page.tsx + hermes-download-page.tsx). |
| 4 | Flujo 4 — Suspender/reactivar con corte de acceso real | **VERIFICADO (re-producido de primera mano en este run)** | Ciclo ejecutado AHORA por QA vía API: `POST .../suspend` → 200, psql `status=suspended`; con el tenant suspendido `GET /download/qa-pilar1-review/bundle` → **403** `{"reason":"tenant_suspended","message":"This organization is currently suspended.","request_id":"req_4149e7fbd85649f8aaf5"}`; `POST .../reactivate` → 200, `status=active`, bundle → **200**. Audit de ESTE ciclo: `tenant.suspended` + `tenant.reactivated` (provider, 2026-10-27 14:49:44 UTC), más los 2 ciclos del reporte (19:27:32/19:27:48) presentes en BD. Código del corte: `tenant_suspended()` en `platform/app/errors.py:102`, raise en `auth/service.py:426`, `devices/router.py:88`, `download/router.py:178`. |
| 5 | Flujo 5 — Link de descarga por tenant (spec 023) | **VERIFICADO** | `GET /download/qa-pilar1-review` → **200** público. Anti-enumeración: `GET /download/no-such-tenant-xyz` → **404**. `GET /download/qa-pilar1-review/bundle` → **200**, 63459 bytes `application/gzip`, tarball real: `install.sh`, `trust-login`, `plugin/trust-agent-governance/*` (14 archivos), README, `manifest.json`. Manifest: `org_code: "qa-pilar1-review"`, `slug`, `oidc_issuer_url: .../realms/qa-pilar1-review`, `members_sha256` por miembro, `spec: "023-hermes-download"` — binding al tenant correcto. Audit: `download.issued` ×2 (system), `download.link.disabled`/`download.link.enabled` (org_admin, 01:23:53/01:24:14 UTC — coincide con el reporte). Suite re-ejecutada por QA en este run: `platform/.venv/bin/python -m pytest tests/test_hermes_download.py -q` → **9 passed** (3.64s). |
| 6 | Hallazgo 1 — Copy OTA corregido (t_ccf901f4) | **VERIFICADO** | Copy nuevo en el fuente `platform/provider-console/src/app/(provider)/tenants/new/provision-form.tsx:241-244` y **servido en vivo** por el contenedor `trust-agent-provider-console`: chunk `.next/static/chunks/2s3ygaumefdoh.js` contiene "TOTP (MFA) enrollment is enforced only when the organization's policy requires MFA (require_mfa=true)". Copy viejo ausente: "forces a password change and TOTP" → 0 apariciones; "mandatory TOTP" → 0 apariciones (chunks del contenedor y builds locales). |
| 7 | Hallazgo 2 — MFA opcional por defecto (INFO, no bug) | **VERIFICADO (coherente)** | Spec `specs/015-auth-ux/` existe; el copy OTA corregido (punto 6) documenta exactamente este comportamiento. No es gap. |
| 8 | Artefactos de evidencia citados por el reporte | **VERIFICADO** | `docs/evidencia/revision-pilar1/hermes-download-toggle-enabled.png` → PNG real (1280×577, 8-bit RGB, 99729 B). `download-page-disabled.html` → 8026 B con el mensaje honesto "disabled new downloads. Please contact them if you need the app." |
| 9 | Specs del pilar presentes (SDD) | **VERIFICADO** | `specs/014-provider-console/`, `specs/015-auth-ux/`, `specs/022-hermes-real-bridge/`, `specs/023-hermes-download/` (con spec.md + plan.md + tasks.md) existen en `/root/trust-agent/specs/`. |
| 10 | Tareas que cerraron el pilar — convergencia en el board | **VERIFICADO** | SQL directo a `boards/trust-agent/kanban.db`: t_fb31dc49 **done** (qa), t_526030f3 **done** (dev, backend spec 023), t_ccf901f4 **done** (ui, copy OTA), t_4151f562 **done** (ui, Provider Console), t_360a752e **done** (ui, mudanza download link). Gate t_e8a28fce: `todo` (pendiente, como corresponde — no es gap del Pilar 1). |

## Observaciones menores (no gaps)

- El bundle descargado hoy pesa **63,459 bytes** vs "32 KB" citado en el reporte del 26-10: el bundle creció (plugin 0.5.0 con más módulos de enforcement). El contenido y el binding al tenant son correctos; no contradice el PASS del flujo 5.
- La lista de tenants pasó de 48 (momento del reporte) a 78 filas hoy: consecuencia de tenants aprovisionados por otros runs de QA; consistente.
- El primer login PKCE+`UPDATE_PASSWORD` del admin (flujo 1) no fue re-ejecutado en este run; la evidencia vigente es el audit `auth.login` (org_admin) en BD + realm KC vivo con endpoint OIDC 200.

## Veredicto global del Pilar 1

**SÍ — completo y verificado e2e.**

Justificación basada solo en evidencia: los **5/5 flujos** del reporte de cierre quedaron verificados contra el estado real, y dos de ellos (flujo 4 suspend/reactivate con corte 403 `tenant_suspended`, y flujo 5 endpoint público + bundle + anti-enumeración + suite 9/9) fueron **re-producidos de primera mano en este run** con resultados idénticos a los reportados. Los dos hallazgos del reporte están cerrados y verificados en vivo (copy OTA servido por el contenedor; MFA opcional coherente con spec 015). Las 5 tareas que cerraron el pilar están `done` en el board (verificado por SQL). **0 gaps abiertos** del Pilar 1. Las specs 014/015/022/023 existen con su tríada SDD.

El único punto no re-ejecutado en este run (primer login PKCE del admin del flujo 1) tiene evidencia de audit en BD y realm vivo, y fue verificado con navegador real por t_fb31dc49/t_526030f3; no constituye un gap.

## Cómo reproducir esta verificación

```bash
# Stack vivo + realm
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/healthz                                  # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/realms/qa-pilar1-review/.well-known/openid-configuration  # 200

# Estado del tenant + audit
docker exec trust-agent-db psql -U postgres -d trust_agent -c \
 "SELECT action,actor_role,count(*),max(ts)::timestamp(0) FROM audit_log WHERE tenant_id=(SELECT id FROM tenants WHERE slug='qa-pilar1-review') GROUP BY 1,2 ORDER BY 1;"

# API del provider (flujos 2-3)
TOKEN=$(grep -E '^TRUST_PROVIDER_TOKEN=' /root/trust-agent/.env | cut -d= -f2-)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/admin/tenants/qa-pilar1-review | python3 -m json.tool

# Flujo 4 en vivo (corte real): suspend → 403 tenant_suspended → reactivate → 200
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/admin/tenants/qa-pilar1-review/suspend
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/download/qa-pilar1-review/bundle   # 403
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/admin/tenants/qa-pilar1-review/reactivate

# Flujo 5 en vivo
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/download/qa-pilar1-review            # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/download/no-such-tenant-xyz          # 404
cd /root/trust-agent/platform && .venv/bin/python -m pytest tests/test_hermes_download.py -q        # 9 passed

# Copy OTA servido (hallazgo 1)
docker exec trust-agent-provider-console sh -c 'grep -rlo "enforced only when" .next/static/chunks/'

# Board
sqlite3 /root/.hermes/kanban/boards/trust-agent/kanban.db \
 "SELECT id,status FROM tasks WHERE id IN ('t_fb31dc49','t_526030f3','t_ccf901f4','t_4151f562','t_360a752e');"
```
