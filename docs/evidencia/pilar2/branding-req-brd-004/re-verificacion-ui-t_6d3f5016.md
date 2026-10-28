# Re-verificación UI (t_6d3f5016) — fix "Save branding" absorbido por el flujo draft/publish

**Tarea**: t_6d3f5016 (fallo QA Pilar 2 §9: "Save branding" no llamaba a `PUT /api/branding` — toast de éxito falso) · **Fecha**: 2026-10-26 · **Perfil**: ui

**Contexto**: el fix original de esta card (inputs controlados + PUT real + toasts honestos) fue absorbido por t_d2b65bcf (spec 028, REQ-BRD-004) como base del flujo draft/publish. Esta re-verificación confirma, con evidencia propia y contra el árbol actual, que el fallo QA está cerrado bajo el contrato nuevo: `PUT /api/branding` edita el draft, `POST /api/branding/publish` publica.

**Fixture**: tenant `console-smoke2`, usuario `admin@console-smoke2.test` (org_admin). Login 100% REAL: baile PKCE S256 + one-time password (Admin API reset) + enrolamiento TOTP RFC 6238 real (secret `J33eLbgz…`, código `191362`) + callback mint → cookies de sesión MFA inyectadas en un navegador real (Chromium vía CDP). Script: variante de `platform/console/scripts/smoke_console.py` (workspace de la tarea).

## Pasos del QA reproducidos contra el flujo draft→publish (nada simulado)

1. **`/branding` con sesión real**: `published: v3 · source: tenant | draft: in sync` (estado heredado de la evidencia de t_d2b65bcf: v3 = rollback a v1, "Smoke2 Branding Corp").
2. **Editar** Product name → `Pilar2 UI Corp`, Accent color → `#0F766E` (los valores exactos del repro de QA) → click **Save draft**.
   - `performance.getEntriesByType('resource')` registró **1 entrada nueva**: `PUT http://127.0.0.1:3100/api/proxy/api/branding` (fetch) — el fallo original (CERO requests) está muerto.
   - Toast de éxito SOLO tras el 200 real: `Draft saved — publish to make it visible to users.` + refetch (`/api/branding/draft`, `/api/branding/versions`).
   - Badge `draft: unpublished changes` con `published: v3` intacto — el draft NO es visible para usuarios (REQ-BRD-004).
   - **Diff de solo claves cambiadas verificado**: el audit registró `branding.draft.updated {"keys":["product_name"]}` — `accent_color` NO viajó porque `#0F766E` ya era el valor vigente del draft (comportamiento esperado del payload diferencial).
   - Bonus (manejo honesto de errores): con la cookie CSRF inaccesible para JS (artefacto de la inyección CDP), el PUT devolvió 403 real `csrf_required` y la UI mostró el toast de error del control plane tal cual — ningún éxito falso.
3. **Publish** → `POST /api/proxy/api/branding/publish` (entrada nueva en performance) → toast `Published as version 4 — visible to all users.` → badges `published: v4 · source: tenant | draft: in sync`.
4. **Reload duro** de `/branding`: inputs muestran `Pilar2 UI Corp` / `#0F766E`; el sidebar aplica el brand runtime (`Pilar2 UI Corp`); historial: `v4 · Pilar2 UI Corp (live)`, v3/v2/v1 preservadas (append-only).

## Persistencia en DB (psql contra trust-agent-db, verificado)

- `tenants.branding` (publicado): `{"product_name": "Pilar2 UI Corp", "accent_color": "#0F766E", "tagline": "Governed AI, our way", logos/favicon asset ids…}`, `branding_version = 4`.
- `tenants.branding_draft`: idéntico al publicado (in sync tras publish).
- `branding_versions`: fila nueva `v4 · data.product_name = "Pilar2 UI Corp" · published_at 2026-10-27 03:01:49 UTC`; v3 (`Smoke2 Branding Corp`) intacta.

## Audit (audit_log del tenant, orden de cadena)

```
branding.draft.updated   org_admin {"keys": ["product_name"]}
branding.published       org_admin {"keys": ["accent_color","favicon_asset_id","logo_dark_asset_id","logo_light_asset_id","product_name","tagline"], "version": 4}
```

## Código y spec

- `branding-page.tsx` actual: los 3 inputs son controlados (`valueOf`/`edit`), el botón dispara `PUT /api/branding` real vía proxy con payload diferencial, toasts solo tras 200/errores ApiError tal cual, refetch tras guardar, preview reactivo, validación cliente espejo de `branding.py`, comentario cabecera actualizado (ya no dice "el endpoint NO existe"). El botón "Save branding" original fue sustituido por "Save draft" + "Publish" (contrato spec 028).
- Spec 009 amendada al contrato draft→publish: REQ-AC-020 (Enmienda 2), **AC-AC-020** reescrito (PUT→draft + audit `branding.draft.updated`; Publish→`branding.published` vN+1; visibilidad solo tras publish), tabla §4.2 `GET/PUT /api/branding` → **Implementado**, T20 de tasks.md con nota de re-verificación.

## Checks

- Console: `npm run lint` limpio (0 errors) + `npm run build` OK (Next.js 16.4.3, 21 rutas, exit 0).
- Captura: `re-verificacion-ui-branding-post-publish.png` (/branding tras publish v4: badges, inputs, preview, historial).

## Veredicto

**FAIL original de QA cerrado**: el click de guardado dispara requests HTTP reales (PUT y POST verificados en el performance buffer del navegador), la persistencia existe en DB (draft + publicado + versión), el audit registra los eventos, y la UI refleja lo publicado tras reload. Bajo el contrato REQ-BRD-004, "la UI refleja el cambio tras reload" aplica tras Publish — verificado.
