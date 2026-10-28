# Evidencia E2E — spec 036: panel de políticas legibles

**Fecha**: 2026-10-28 · **Perfil**: ui (t_f0d2335a) · **Nada simulado** (constitution I):
login real authorization-code+PKCE con enrolamiento TOTP RFC 6238, tenant real
`policies-ux` (Policies UX Corp) provisionado por la provider API, PUT/DELETE
reales del control plane, Chromium real vía Playwright.

## Cómo reproducirlo

```bash
# 1. Sesiones reales (PKCE + one-time + TOTP; idempotente por slug):
cd platform/console && ../.venv/bin/python scripts/e2e_policies_ux.py \
  "PoliciesUxOta2026!" /tmp/policies_ux_state.json
# → [PASS] login #1 real (PKCE + one-time + enrolamiento TOTP)
# → [PASS] presentation cobertura 1:1 con el catálogo (47/47)
# → [PASS] 9 categorías legibles / org_effective / departments[].effective
# → [PASS] login real dept_admin (PKCE+TOTP)

# 2. Checks API de los gates del engine (AC-036-003):
../.venv/bin/python scripts/e2e_policies_ux_api_checks.py /tmp/policies_ux_state.json

# 3. UI real en Chromium (16 checks DOM + 4 capturas full-page):
NODE_PATH=/usr/local/lib/hermes-agent/node_modules node scripts/e2e_policies_ux_shots.mjs \
  /tmp/policies_ux_state.json docs/evidencia/policies-ux/
```

Resultados máquina-verificables: `policies-ux-e2e-results.json` (16/16 PASS).

## AC-036-001 — catálogo legible canónico (API+test)

- `sha256sum` de `platform/app/governance/policy_catalog_readable.json` ==
  `specs/036-policies-ux/policy-catalog-readable.json`:
  `3c108c1afbf7a8fd7a9312c6186997e681f4af6e5df7ce802557224a10535e40`.
- `tests/test_policies_presentation.py` (6 passed):
  - cobertura 1:1 `presentation` ↔ `catalog.policy_keys` — 47/47 sin
    faltantes ni sobrantes (falla en CI si diverge);
  - sha256 plataforma == contrato de la spec;
  - org_only == `ORG_ONLY_KEYS`, invariable == DB, enums == combinator;
  - `org_effective`/`departments[].effective` computados con `combine`;
  - dept_admin también recibe `presentation`.
- En vivo: `[PASS] presentation cobertura 1:1 con el catálogo (47/47)`.

## AC-036-002 — panel tipo app store (E2E, Chromium)

- Vista por defecto de `/policies` = tarjetas legibles; **9 categorías**
  visibles (Security & session … Users & access) + sección final
  **Provider protections**.
- **0 keys crudas**: los 47 slugs grepeados contra `document.body.innerText`
  → 0 apariciones (`leaked=[]`).
- Buscador es+en: "mfa" filtra a exactamente la card
  "Multi-factor authentication (MFA)"; "multifactor" (texto es del contrato)
  también la encuentra.
- org_admin conmuta "Technical view" → tabla maestra spec 033
  (Key/Type/Provider default/Organization/Value/Origin); "Policy cards" vuelve.
- **dept_admin NO ve el conmutador** (count=0) y ve el panel legible por
  defecto.
- Capturas: `policies-ux-default-panel.png`, `policies-ux-dept-admin.png`.

## AC-036-003 — asignador "Aplicar a" (E2E + API)

- Asignador `Apply to`: Organization / Department / User + picker de
  destinatario; escribe SOLO con los PUT/DELETE existentes del nivel.
- Ejemplo de Miller (ítem, no key): `allowed_tools` del worker con
  `web_search` AUSENTE — `GET /api/policies/effective` confirma
  `effective=["governance_status","read_file","write_file"]`,
  `source="user"`, `tightened_by_user=true`; la card muestra el sub-toggle
  "✗ Web search" con badge "Set for this user".
- `require_mfa` (org_only): la card muestra badge "Organization-wide" +
  "Defined at organization level — edits apply to everyone"; PUT a nivel
  usuario → **422 real** (gate existente): `[PASS] PUT require_mfa a nivel
  usuario → 422`.
- Invariables: sección "Provider protections" con las 3 cards
  (Approved models only / Explicit grants guaranteed / Least-privilege
  credentials), badge "Provider protection", candado, "Fixed by the
  provider — always on", y **cero controles** (0 switch/select/input);
  PUT `approved_models_only=false` a org → **422 real**.

## AC-036-004 — 422 legible inline (E2E)

- Setup real: org fija `max_daily_sessions=5` (PUT real; la card org muestra
  "Up to 5 sessions per day — Set by the organization").
- A nivel usuario (worker) se intenta relajar a 20 → el control plane
  responde **422 real** y la tarjeta muestra INLINE (role=alert):
  - mensaje real: "User policy would relax a superior policy for
    'max_daily_sessions' (REQ-POL-007)."
  - explicación en palabras: "A lower level can only tighten (restrict) what
    a higher level already set — it cannot relax it. The control went back
    to the current value."
- **El control vuelve al valor vigente**: el input regresa a "5"
  (verificado: `inputValue="5"`). Sin toast genérico.
- Captura: `policies-ux-422-inline.png`.

## AC-036-005 — vista por destinatario (E2E)

- Worker (`worker@policies-ux.test`): la lista muestra frases derivadas del
  valor EFECTIVO del backend (`GET /api/policies/effective`), p. ej.
  "Must sign in with MFA" + badge "Inherited from organization",
  "Device must check in every 24 hours", "Session expires after 60 minutes
  of inactivity", "Web browsing: Open — any site", "Runs commands in:
  Isolated Docker container".
- Multiselects como sub-toggles por ítem ✓/✗ (D-36-08): "✗ Web search /
  ✓ Read files / ✓ Write files / ✓ View governance status" con badge
  "Set for this user" (origen user, tras refetch real).
- Captura: `policies-ux-user-view.png`.

## AC-036-006 — engine intacto (review+CI)

- `git diff --stat combinator.py policies.py` → **vacío** (cero cambios);
  la carga del JSON es read-only (`policy_presentation.py`, `lru_cache`).
- Cero mutaciones nuevas: la UI escribe solo con los PUT/DELETE existentes.
- Suite: `test_policies_presentation.py` + `test_policies_matrix.py` +
  governance → verde; cadena E2E `test_control_plane_flow.py` 10/10.
- `GET /api/policies/effective` sin cambios de contrato.

## AC-036-007 — robustez y bilingüe (unit/test)

- `node scripts/check_policy_icons.mjs` → "9 categorías, 47 keys, 49 iconos
  únicos. OK: los 49 iconos existen en lucide-react" (fallback a icono de
  categoría disponible en `policy-icons.tsx`).
- Fallback sin presentación: nuevo test
  `test_matrix_tolerates_key_without_presentation` (monkeypatch que dropea
  `allowed_tools` del contrato) — matrix sigue 200 sirviendo la key en el
  catálogo y sin bloque en `presentation` (degradación grácil; la UI
  renderiza el control genérico por `value_type` con badge "Untranslated",
  `resolvePresentation` + `RawControl`).
- Contrato completo es+en (test de campos obligatorios por locale).

## AC-036-008 — estándar UI Pro (build+E2E)

- `npm run build` exit 0 (31/31 páginas); `npx eslint src/app/(console)/policies/**`
  limpio; `npm run lint` del árbol tiene 1 error preexistente en
  `src/components/structure-wizard.tsx` (WIP spec 038 de otro perfil, archivo
  untracked — no de esta spec).
- Navegación por teclado: Tab recorre select → input → button sin trampas de
  foco (verificado en Chromium); switches con `role="switch"` +
  `aria-checked`, alerts con `role="alert"`, estados loading (skeletons) y
  error reales.

## AC-036-009 — nada simulado (review)

- `grep` de frases legibles ("Must sign in with MFA", "Web search",
  "Approved models only", "Smart approval") en
  `console/src/app/(console)/policies/` → **0** (todo viene del bloque
  `presentation` del backend; la UI no hardcodea nombres/frases).
- Este documento + los 4 PNG + `policies-ux-e2e-results.json` son la
  evidencia del login PKCE+TOTP real documentada.

## Notas de entorno

- El restart del contenedor `console` invalida las cookies de sesión
  (store del BFF): re-mintear con el paso 1 si se reproducen las capturas
  tras un rebuild.
- `tests/test_vault_connectors.py` y `test_model_providers.py` muestran
  errores solo en corrida completa del árbol compartido (WIP spec 038 de
  otro perfil en el mismo árbol + contención de DB); pasan aislados y no
  pertenecen a esta spec.
