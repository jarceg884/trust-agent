# Evidencia E2E — Escenario "Practicantes" EN VIVO (spec 041, REQ-041-017 / AC-041-017)

Fecha: 2026-10-29 · Perfil: qa · Tarea: t_5fc35831 (fase implement+converge E2E)

Escenario Miller completo contra el stack VIVO — nada simulado
(constitution I / REQ-041-016): tenant real `policies-ux`, usuarios reales
(admin org_admin + worker con MFA), dispositivo Hermes REAL enrolado
(keypair RSA-2048, aprobación admin, device token), gateway LiteLLM REAL
(:4000, upstream Huawei Cloud MaaS) y Chromium real (Playwright) para la UI.

## Resultados

| Suite | Resultado |
|---|---|
| `e2e_041_practicantes_ui.mjs` (creación+asignación por la UI real) | **19/19 PASS** |
| `evidence_041_practicantes_live.py` (enforcement en vivo) | **29/29 PASS** |
| Convergencia UI: `e2e_named_policies_shots.mjs` (spec 041) | **24/24 PASS** |
| Convergencia UI: `e2e_policies_ux_shots.mjs` (regresión 036/033) | **17/17 PASS** |
| Convergencia UI: `check_policy_icons.mjs` (contratos 036+041) | **OK** (49+49) |
| Convergencia UI: `npm run build` / lint | exit 0 / 0 hallazgos en archivos 041 (1 error + 6 warnings preexistentes en structure-wizard/login-form/mfa-setup/focus-sweep, WIP de otras lanes) |
| Convergencia backend: `pytest tests/test_named_policies.py` (AC-041-001…011) | **16 passed** (re-run independiente QA) |
| Convergencia backend: governance (policies_api/matrix/presentation/catalog_v2/governance/bundle) | **63 passed** |
| Engine intacto (AC-041-010) | `git diff` VACÍO en `combinator.py` y `policies.py` (solo archivos nuevos + wiring aditivo en `router.py`) |

## El escenario, paso a paso (con evidencia)

1. **Login real PKCE+TOTP (acr=2)** — admin (sesión console MFA-verificada,
   `ta_console_*`) y worker (re-enrolamiento TOTP RFC 6238 inline, id_token
   acr=2, sesión mintada). Trazas en `practicantes-live.log` R1.
2. **Creación de "Practicantes" desde la UI** (Chromium real): "+ New policy"
   → nombre `Practicantes` → **from template `restringido-total`** (D-41-08,
   "el admin parte de una y ajusta") → composición en el editor:
   - SIN internet: "Web search" OFF (allowed_tools sin `web_search`) +
     "Agent web browsing" ON en modo **allowlist con lista vacía**
     (`web_domain_policy {"mode":"allowlist","domains":[]}`).
   - SIN terminal: "Where commands run" ON con los 3 backends OFF
     (`allowed_terminal_backends []`).
   - SOLO el modelo aprobado elegido: "Available AI models" ON (requires_choice)
     → **GLM-5.2** (único incluido; el control lista los 7 modelos del tenant
     desde `GET /api/models`, nada hardcodeado).
   - `approval_mode: "manual"`.
   - Capturas: `practicantes-01-editor.png` (editor compuesto),
     `practicantes-02-domains-control.png` (control de dominios, ver
     Hallazgo UI abajo).
   - Verificación API: definition EXACTA de 16 keys
     (`practicantes-ui-results.json`).
3. **Asignación al usuario real** worker@policies-ux.test: "Assign to…" →
   User → worker → **preview** ("Will be written (17)",
   "nothing is written until you confirm") → Confirm → resultado legible
   ("Applied to user"). Capturas: `practicantes-03-assign-preview.png`,
   `practicantes-04-assign-result.png`.
4. **Verificación EXACTA en vivo** (`practicantes-live.log`):
   - (a) `GET /api/policies/effective?user_id=<worker>` (admin) y
     `GET /api/me/policies` (worker): `allowed_tools
     [governance_status, read_file, write_file]` (SIN web_search),
     `allowed_terminal_backends []` (`denied: true`),
     `allowed_models ["glm-5.2"]`, `approval_mode "manual"`,
     `web_domain_policy {"allowlist", []}` — todo `source: "user"`.
   - (b) **Hermes enrolado** (perfil `trust-live-prac`, dispositivo REAL
     `13aa36df-3ead-428d-99ac-f86699c54787`, fp `f3a1bcbc513e4cd1…`,
     name `ecs-hermes`, status `active`, bundle JWS `policy_version=29`):
     sesión live → **web_search BLOQUEADA** y **terminal BLOQUEADO**
     (2 × "BLOCKED by organization policy" en stdout; plugin.log:
     `not in allowed_tools` ×2, `web_search_unrestricted`,
     `approval_mode_below_floor` — vetos de las keys de Practicantes).
   - (c) **glm-5.2 (incluido) PASA por el gateway LiteLLM real**: HTTP 200
     con usage real de MaaS (R3) y sesión live completa del Hermes enrolado
     con respuesta del modelo (R5, sin veto de provider).
   - (d) **Modelos aprobados NO incluidos BLOQUEADOS**: `glm-5.3` y
     `deepseek-v4.1-flash` → HTTP 403 del gateway ("not available for this
     API key" — la key virtual del worker quedó restringida a
     `["glm-5.2"]` por la intersección con la policy en la emisión,
     `POST /api/models/keys` → `allowed_models: ["glm-5.2"]`); sesión live
     con default `glm-5.3` → el gateway rechaza la llamada (R6).
   - Audit: `named_policy.created` + `named_policy.assigned` con
     `name: Practicantes` y `keys[]` en `audit_log` (R8).
5. **AC-041-014**: chip "Practicantes" en la vista del usuario + frases
   efectivas coherentes ("✗ Web search" + badge "Set for this user").
   Captura: `practicantes-05-recipient-chips.png`.

## Cómo reproducir

```bash
# 0. Sanity del stack + sesión admin (cookies en /tmp/builder-state.json,
#    mintadas por scripts/e2e_policies_ux.py con login PKCE+TOTP real).
cd platform && .venv/bin/python scripts/evidence_041_practicantes_sanity.py

# 1. Convergencia UI (suites de la fase UI, re-ejecutadas por QA):
cd console && python3 scripts/e2e_policies_ux_setup_state.py /tmp/builder-state.json
node scripts/e2e_named_policies_shots.mjs /tmp/builder-state.json <outdir>   # 24/24
python3 scripts/e2e_policies_ux_setup_state.py /tmp/builder-state.json      # (estado del check 036-005)
node scripts/e2e_policies_ux_shots.mjs /tmp/builder-state.json <outdir>     # 17/17
node scripts/check_policy_icons.mjs && npm run build

# 2. Escenario Practicantes por la UI real (Chromium):
node scripts/e2e_041_practicantes_ui.mjs /tmp/builder-state.json <outdir>   # 19/19

# 3. Enrolamiento REAL del Hermes del worker (perfil trust-live-prac):
#    login PKCE+TOTP acr=2 + id_token 0600 + sync del plugin
.venv/bin/python scripts/evidence_041_practicantes_setup.py /tmp/prac-state.json
#    key virtual restringida por policy + config.yaml del perfil al gateway :4000
.venv/bin/python scripts/evidence_041_practicantes_key.py /tmp/prac-state.json
#    gateway del perfil arriba (hermes -p trust-live-prac gateway run,
#    gateway.standalone: true) → auto-enroll zero-UI → aprobar el pending:
.venv/bin/python scripts/evidence_041_practicantes_approve.py

# 4. Evidencia en vivo (29 checks: effective, vetos, gateway, audit):
.venv/bin/python scripts/evidence_041_practicantes_live.py /tmp/prac-state.json

# 5. Convergencia backend (independiente):
TRUST_TEST_DB=<db> .venv/bin/pytest tests/test_named_policies.py -q          # 16 passed
TRUST_TEST_DB=<db> .venv/bin/pytest tests/test_policies_api.py tests/test_policies_matrix.py \
  tests/test_policies_presentation.py tests/test_policy_catalog_v2.py \
  tests/test_governance.py tests/test_governance_bundle.py -q               # 63 passed
```

## Hallazgo UI (no bloqueante, card creada para ui)

El control de dominios de spec 036 que el builder reutiliza
(`platform/console/src/app/(console)/policies/readable/policy-control.tsx:341-345`,
`DomainsControl.apply`) **rechaza aplicar una allowlist vacía** ("Add at
least one domain, or switch the mode back to open"), pero REQ-041-017 exige
`web_domain_policy` allowlist vacía y la propia plantilla `restringido-total`
del contrato la usa. El valor es válido para el backend; la composición desde
una política EN BLANCO está bloqueada por el control — solo se obtiene
copiando una plantilla (vía D-41-08, que es el flujo de Miller y el usado en
esta evidencia). Card: ver kanban (perfil ui). Captura del error inline:
`practicantes-02-domains-control.png`.

## Estado final del tenant (dejado como demo en vivo)

- Política nombrada **Practicantes** asignada a worker@policies-ux.test
  (chip visible en el console).
- Dispositivo `ecs-hermes` (perfil `trust-live-prac`) activo y enrolado al
  worker; su gateway standalone corre con el plugin
  `trust-agent-governance` sincronizado.
- Key virtual del worker restringida a `["glm-5.2"]` en el gateway real.
