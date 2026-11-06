# Evidencia E2E — Policy Builder de políticas nombradas (spec 041, fase UI)

Fecha: 2026-10-29 · Perfil: ui · Tarea: t_e917982c

Evidencia de la fase **implement UI** de la spec 041 contra el stack vivo
(tenant `policies-ux`, login real PKCE + TOTP — nada simulado, REQ-041-016).
El escenario completo "Practicantes" en vivo (REQ-041-017, con Hermes
enrolado verificando enforcement EXACTO) es de la tarea e2e (t_5fc35831).

## Cómo reproducir

```bash
# 1. Sesiones reales (PKCE + one-time + enrolamiento TOTP; exporta cookies)
cd platform/console && ../.venv/bin/python scripts/e2e_policies_ux.py \
    OneTimeBuilder2026! /tmp/builder-state.json

# 2. Estado base que asumen las verificaciones (PUTs reales)
python3 scripts/e2e_policies_ux_setup_state.py /tmp/builder-state.json

# 3. Builder spec 041 (24 checks) — crea/edita/asigna/reaplica/borra
#    "E2e Builder Policy" por la UI real y verifica contra la API.
node scripts/e2e_named_policies_shots.mjs /tmp/builder-state.json <outdir>

# 4. Regresión spec 036 (17 checks) — panel legible intacto bajo la
#    pestaña "Individual settings" + vista técnica solo org_admin.
node scripts/e2e_policies_ux_shots.mjs /tmp/builder-state.json <outdir>

# 5. Iconos de ambos contratos en lucide-react + mapa del design system
node scripts/check_policy_icons.mjs
```

## Resultados (2026-10-29, imagen trust-agent/console:local rebuild)

| Suite | Resultado |
|---|---|
| `e2e_named_policies_shots.mjs` (spec 041 UI) | **24/24 PASS** |
| `e2e_policies_ux_shots.mjs` (regresión spec 036/033) | **17/17 PASS** |
| `check_policy_icons.mjs` (contratos 036 + 041) | **OK** (49+49 iconos) |
| `npm run build` | exit 0 |
| `npm run lint` | 0 hallazgos en los archivos de esta tarea |

## Cobertura de ACs de UI (REQ-041-012…016)

- **AC-041-012**: lista con las 3 plantillas sembradas + badge Template;
  "+ New policy" en blanco → editor con los 6 grupos y 47 capacidades del
  bloque `builder` (verificado contra la API en vivo — nada hardcodeado);
  activar "Web search" compone `allowed_tools` con los 4 ítems (hermanos
  `default_on`, D-41-03) visibles en el resumen; scalar ON muestra su
  control 036 (docker/local/ssh); guardar sin elegir modelo → error inline
  y CERO POST (verificado por conteo de políticas).
- **AC-041-013**: assign a usuario con preview del diff (keys a escribir,
  "Replaces:" sobre el override existente, aviso de `would_relax`),
  `require_mfa` omitida con "Skipped — organization-level only";
  confirmación → resultado legible; `GET /api/policies/effective` del
  worker refleja EXACTO lo materializado (web_search ON a nivel user,
  MFA sigue a nivel org).
- **AC-041-014**: chips de la policy nombrada en la vista por destinatario
  (pestaña Individual settings, usuario worker) junto a las frases efectivas.
- **D-41-06**: editar → badge "Outdated" + "Apply changes" → reapply por
  destinatario con resultado legible.
- **D-41-07**: borrar con asignaciones → 409 con destinatario real +
  "Unapply everywhere and delete" (cascade=unassign) → la policy desaparece
  y la unassign desactiva las keys del snapshot (effective vuelve a
  heredar).
- **AC-041-015**: dept_admin ve el builder pero SIN opción "Organization"
  en el asignador (D-41-09); navegación por teclado (flechas entre
  pestañas, Tab por controles); estados loading/403/409/422 reales.
- **AC-041-016**: grep sin literales de grupos/capacidades/defaults/
  plantillas en el código del builder (solo nombres de campos del esquema
  y comentarios); todo el render proviene de `GET /api/named-policies`,
  `GET /api/policies/matrix`, `GET /api/models`, `GET /api/connectors`,
  `GET /api/vault/connections`, `GET /api/named-policies/recipients`.

## Capturas

| Archivo | Qué muestra |
|---|---|
| `builder-01-list.png` | Lista por defecto de /policies con las 3 plantillas |
| `builder-02-blocked-save.png` | Error inline "Choose a value for: Available AI models" |
| `builder-03-editor-saved.png` | Editor con sidebar de 6 grupos + resumen compuesto |
| `builder-04-assign-preview.png` | Preview del diff con "Replaces:" y skipped org-only |
| `builder-05-recipient-chips.png` | Chips de la policy en la vista por destinatario |
| `builder-06-stale.png` | Badge "Outdated" + "Apply changes" tras editar |
| `builder-07-delete-409.png` | Diálogo 409 con "Unapply everywhere and delete" |
| `builder-08-dept-admin.png` | Asignador de dept_admin sin opción Organization |

## Fase E2E en vivo (tarea t_5fc35831)

El escenario completo "Practicantes" EN VIVO (REQ-041-017: Hermes enrolado,
gateway LiteLLM real, dispositivo real) y la convergencia de TODOS los ACs
están documentados en **`practicantes-e2e.md`** (junto a
`practicantes-live.log`, `practicantes-ui-results.json`,
`practicantes-0*.png` y `converge-qa/` con el re-run de las suites de esta
fase UI: 24/24 + 17/17 + iconos + build).
