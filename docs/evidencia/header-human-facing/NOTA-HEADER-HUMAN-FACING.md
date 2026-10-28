# Header del Admin Console "human-facing" — tenant_name en whoami + nombre real en el chip

**Tarea:** t_b14cbefb (follow-up del UI polish v2, t_3cf085b9 § "Deuda honesta").
**Fecha:** 2026-10-27 · **Lane:** dev · **Estado:** convergido y verificado.

## Contexto

El vision model (deepseek-v4.1-flash) marcó el header del Admin Console como
"machine-facing" — su top-1 issue restante en la pantalla Hermes Download
(8/10). Dos causas raíz:

1. El topbar mostraba el `tenant_slug` crudo (`hermes-dl`) porque
   `GET /api/me` (whoami) no exponía el nombre de la organización.
2. El chip de usuario mostraba `admin` (parte local del email) porque el
   usuario de evidencia no tiene `display_name` propio (el control plane
   persiste `display_name = email` cuando no hay nombre).

## Qué cambió

### Backend — campo aditivo `tenant_name` en whoami

- `platform/app/auth/service.py`: `Principal` gana `tenant_name` (join con
  `tenants.name`, misma tabla que el slug). Poblado en los dos caminos de
  autenticación (sesión PostgreSQL vía `lookup_session` y JWT stateless) y
  en el operador de proveedor (`tenant_name="Platform"`).
- `platform/app/governance/bundle.py` y `platform/app/models/keys.py`:
  los otros dos constructores de `Principal` (device token del plugin y
  API keys) pueblan el campo desde la misma fuente.
- `platform/app/auth/router.py` (`whoami`): respuesta con
  `"tenant_name"` junto a `tenant_slug`.

**Decisión de contrato (revisado antes de tocar el shape):** whoami NO está
congelado en specs 001/012 — la única referencia de `/api/me` en 001 es
AC-POL-006 (`/api/me/policies`, otro endpoint) y 012 no menciona whoami.
Las respuestas del control plane son dicts sin `response_model`, así que
una clave ADITIVA es contract-safe para los consumidores existentes
(console, desktop, smoke scripts): quien no la lee no se rompe. No se
tocó ninguna clave existente.

### Console — topbar y chip human-facing

- `platform/console/src/lib/types.ts`: `WhoAmI.tenant_name: string`
  (documentado como aditivo).
- `platform/console/src/components/shell/topbar.tsx`:
  - **Org:** el topbar muestra `tenant_name` ("Hermes Download Evidence
    Corp") como identificador primario; el slug queda degradado a
    tooltip (`title`) + texto secundario pequeño (`text-faint`, solo ≥md).
    Fallback a slug si el backend aún no expusiera nombre.
  - **Chip de usuario:** con nombre real (`display_name !== email`) el
    chip muestra `Nombre — rol` (p. ej. "Ana Torres — org_admin", el rol
    en texto muted). Sin nombre real, mantiene el fallback v2 (parte
    local del email). El email completo sigue viviendo en el menú.

### display_name del usuario de evidencia — decisión

El body sugería dos caminos: poblar `display_name` con firstName+lastName
del IdP en un "sync de usuarios", o mostrar `Nombre — rol` en el chip. Se
implementó el segundo y se **descartó** el sync por tres razones
documentadas:

1. Los usuarios brokered (Google/OIDC) YA se auto-provisionan con
   firstName+lastName del IdP (`auth/router.py::_auto_provision_brokered`).
2. Para el admin creado en el provisioning, Keycloak guarda
   `firstName = (display_name o email).split(" ")[0]` — para un tenant
   provisionado sin `admin_display_name` (el caso de evidencia) el
   firstName del IdP ES el email, así que el sync no aportaría nada.
3. `display_name` ya es editable por el camino real del directorio
   (`PUT /api/users/{id}`, `EDITABLE_FIELDS`, audit incluido) — que es
   exactamente lo que un org_admin haría desde la página Users.

Para la evidencia, el `display_name` del admin de `hermes-dl` se pobló
con **"Ana Torres"** vía esa API REAL (`PUT /api/users/{id}` + cookie de
sesión admin + `X-CSRF-Token`) — mutación real, audit real
(`audit_log`: `user.updated`, actor `org_admin`). Nada simulado: login
PKCE S256 + TOTP real contra el Keycloak 26 vivo (acr=2), sesión admin
mintada en PostgreSQL.

## Verificación (todo contra el stack vivo, nada simulado)

| Check | Resultado |
|---|---|
| Login real PKCE+TOTP (acr=2) + mint de sesión admin | ✅ `mfa_verified=true` |
| `GET /api/me` expone `tenant_name` = "Hermes Download Evidence Corp" | ✅ (campo nuevo, backend desplegado) |
| `PUT /api/users/{id}` display_name → 200 + audit `user.updated` | ✅ |
| whoami re-verificado: `display_name="Ana Torres"` + `tenant_name` | ✅ |
| DOM check topbar (`/hermes-download`): org primaria + tooltip slug + slug secundario + chip "Ana Torres — org_admin" | ✅ |
| DOM check regresión shell: `/overview` `/users` `/branding` `/audit` | ✅ header idéntico y sin roturas |
| Vision check (deepseek-v4.1-flash, rubric fijo 4 checks) | ✅ **4/4 PASS, HEADER-SCORE 9/10** — "the header now reads as human-facing and professional" (ver `vision-check-header.json`) |
| `npm run build` (console) | ✅ exit 0 |
| `npm run lint` (console) | ✅ exit 0 |
| Tests backend: `test_auth_ux` (incl. `test_whoami_exposes_tenant_name` nuevo), `test_isolation`, `test_rbac`, `test_governance_bundle`, `test_devices_flow`, `test_provider_admin` | ✅ pass |
| Imagen Docker `trust-agent/platform:local` reconstruida + api/workers recreados (`--no-deps`, migrate one-shot roto ajeno a esta lane) | ✅ healthy |
| Imagen `trust-agent/console:local` reconstruida + contenedor recreado | ✅ healthy |

**Flakes de suite completa (honestidad):** corridas full-suite muestran
entre 2 y 12 fallos NO determinísticos que varían entre corridas
(`test_provision::_state` vacío por orden de tests; `test_usage_analytics`
`assert 3 == 5` por contaminación del ledger en el TEST_DB compartido por
lanes concurrentes). Cada archivo fallante pasa aislado y ninguno toca
`Principal`/whoami. El único fallo causado por este cambio
(`test_rbac` construía `Principal` a mano) se corrigió en el mismo patch.

## Artefactos

- `screenshots/01-admin-console-hermes-download-header.png` — captura
  full-page del build final con sesión admin real.
- `vision-check-header.json` — veredicto completo del vision model.
- `mint_and_setup.py` — reproducción: login real + mint + verificación
  whoami + PUT display_name (lee el TOTP secret de la evidencia
  t_360a752e y el provider token del `.env`; sin secretos hardcodeados).
- `vision_check_header.py` — reproducción del vision check (rubric fijo).

## Cómo reproducir

```bash
# 1. Sesión admin real + verificación whoami + display_name vía API real
/root/trust-agent/platform/.venv/bin/python mint_and_setup.py
# 2. Vision check (deepseek-v4.1-flash)
python3 vision_check_header.py
```
