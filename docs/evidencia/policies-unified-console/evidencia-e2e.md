# Evidencia E2E — Policies: página dedicada unificada (spec 033, t_e03bba6c)

**Fecha**: 2026-10-27 · **Perfil**: ui · **Stack vivo**: control plane :8000 (imagen
reconstruida) · Keycloak 26.2 :8080 · PostgreSQL 17 (`trust-agent-db`) · Admin Console
:3100 (imagen reconstruida) · **Nada simulado**: login PKCE S256 + enrolamiento TOTP
RFC 6238 real (variante de `console/scripts/smoke_console.py`), sesión admin MFA
inyectada en Chromium real vía CDP, todas las mutaciones contra la API real.

**Tenant**: `console-smoke2`, admin `admin@console-smoke2.test` (org_admin).

## Nuevo backend read-only (spec 033, REQ-033-001/002)

- `GET /api/policies/matrix` — catálogo tipado completo (47 keys: 18 clásicas + 29 de
  la matriz de capacidades) con `choices` de enums, valores org, overrides por depto
  y por usuario. org_admin completo; dept_admin scoped; `audit.view` sin admin →
  catálogo + baseline org sin deptos/usuarios.
- `GET /api/policies/effective?user_id=…` — efectiva deny-overrides de un usuario
  arbitrario + provenance por key (`source`, `tightened_by_dept/user`, `denied`),
  computada con `combinator.combine` (la UI JAMÁS re-deriva — D-33-01).
- Archivos: `platform/app/governance/policies_matrix.py` (nuevo),
  `governance/router.py` (+2 rutas GET), `platform/tests/test_policies_matrix.py`
  (4 tests: catálogo completo/scoping/403, provenance/tightened/denied, 404 sin
  oráculo, 422 UUID). Suite: **4/4 nuevos verdes**; suite completa 641 passed —
  los 4 fallos de `test_provision.py` son **pre-existentes** (reproducidos idénticos
  con `--ignore=test_policies_matrix.py`: contaminación order-dependent de la suite,
  no causada por este cambio; pasan 10/10 en aislado).

## E2E desde la UI (Chromium real, sesión MFA)

| # | Prueba | Resultado |
|---|--------|-----------|
| 1 | Nav "Policies" en el sidebar (tras Model Access), item activo con acento del tenant | **PASS** |
| 2 | Stats reales: 47 catalog keys · 4 org overrides · 0 dept · 0 user (datos del tenant) | **PASS** |
| 3 | Tabla maestra: 47 filas (key, tipo, default, org, valor del nivel, badges Default/Org), 3 invariables con candado 🔒 y sin Edit (`approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`) | **PASS** |
| 4 | Edición inline org `allow_voice_mode` false→true: PUT real → toast `Policy "allow_voice_mode" saved at org level.` → fila con badge Org + botón Reset | **PASS** |
| 5 | Persistencia: `policies` (org, `true`, v1) en PostgreSQL + audit `policy.updated {"key":"allow_voice_mode","level":"org"}` | **PASS** |
| 6 | No-relajación (REQ-POL-007): org `approval_mode=manual`, dept intenta `off` → **422 real inline**: `Department policy would relax the organization policy for 'approval_mode' (REQ-POL-007).` | **PASS** |
| 7 | Reset (DELETE + ConfirmDialog): override dept `require_mfa` y org `approval_mode`/`allow_voice_mode` → `is_active=f` en DB, valores vuelven al nivel superior/default | **PASS** |
| 8 | Tab "Effective by user": diff del admin — **43 inherited · 0 tightened by dept · 0 tightened by user · 18 denied** (computado por el backend); badges Inherited/Org/Denied por key | **PASS** |
| 9 | Tightening por usuario: override user `session_timeout_minutes=15` (default 60, min gana) → diff muestra `Tightened · user`, contador 0→1; reset → vuelve a 0 | **PASS** |
| 10 | Limpieza: 0 filas de test activas en DB tras el E2E (verificado por query) | **PASS** |
| 11 | Filtro de keys: "mfa" → solo `require_mfa` (client-side sobre el catálogo cargado) | **PASS** |
| 12 | Layout: sin overflow horizontal del documento, 0 celdas clipeadas, wrapper `overflow-x-auto` en ambas tablas | **PASS** |

Capturas: `policies-master-org.png` (tabla maestra, scope org) y
`policies-effective-user.png` (diff del admin) — build final.

## QA visual (deepseek-v4.1-flash vía MaaS, skill vision-check-maas)

3 rondas sobre capturas del build final con rubric fijo (C1 nav / C2 cards /
C3 columnas / C4 badges / C5 coherencia):

- **C1, C2, C5: PASS en todas las rondas.** C3 pasó de FAIL (columnas clipeadas
  sin affordance de scroll) a **PASS** tras el wrapper `overflow-x-auto`.
- Correcciones aplicadas por la crítica: scroll horizontal de tablas; labels de
  cards a una línea ("Dept/User overrides") con baseline alineada y contraste
  `text-muted`; sin IDs de spec en copy de UI ("REQ-POL-007" fuera); pills de
  resumen con tono semántico solo cuando el conteo > 0; selector de usuario sin
  duplicar `display_name`/email; icono de alarma fuera del card de ceros;
  títulos de card distintos de los tabs; vacíos "—" a `text-muted`; filtro de keys.
- Score global de la última ronda ≈ **6.5/10** (juez no determinístico ±1–1.5
  documentado en el skill). Pendientes declarados del producto completo (no de
  esta página): tema claro vs mockup oscuro (shell compartido), contraste AA de
  micro-labels del design system, toasts sin dismiss.

## Observación para dev (semántica pre-existente, fuera de alcance)

Un PUT dept de `require_mfa=false` con org `require_mfa=true` es ACEPTADO (200):
`require_mfa` no está en `TRUE_WINS_KEYS` (spec 029 REQ-029-006 lista solo
`require_write_sandbox`/`require_checkpoints`) y el gate MFA lee solo el nivel org
(`org_policy_value`, spec 015), así que el override dept es inoperante pero visible
en la matriz. Derivado a tarea dev (ver card).

## Cómo verificarlo

```bash
cd /root/trust-agent/platform && .venv/bin/python -m pytest tests/test_policies_matrix.py -q   # 4 passed
cd /root/trust-agent/platform/console && npm run build                                        # exit 0
# stack vivo: docker compose ps (api+console healthy) → login en :3100 → nav "Policies"
```
