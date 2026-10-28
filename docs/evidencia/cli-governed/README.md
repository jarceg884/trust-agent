# Evidencia — Spec 034: Hermes CLI gobernado (t_a51a4622)

Corrida live: **2026-10-27** contra el stack real — control plane :8000,
Keycloak 26.2 :8080, LiteLLM REAL :4000 (v1.106.3), PostgreSQL
(trust-agent-db). Script: `platform/scripts/evidence_cli_governed.py`.
Tenant: `cli-gov-e2e` (CLI Gov Corp, branding "Nimbus Agent").

**Resultado global: 51 PASS, 0 FAIL.**

| Fase | Checks | Qué se verificó (real) |
|---|---|---|
| setup | 8 | Tenant provisionado por el proveedor; admin y worker con login PKCE+TOTP real (acr=2); policy org `allowed_models=[glm-5.2]` + `allowed_tools`; budget worker $20; branding publicado. |
| download | 12 | Página pública anónima con branding y DOS opciones (desktop + comando curl copiable, targets WSL2); `GET /download/{slug}/cli` → script bash con `org_code` horneado, `TRUST_DEVICE_TYPE=cli`, binding y reuso del bundle canónico; `X-Installer-SHA256` = sha256 real; audit `download.issued` `artifact=cli` +1; link deshabilitado → 403 `link_disabled` también para el CLI. |
| install | 12 | **`curl -fsSL http://127.0.0.1:8000/download/cli-gov-e2e/cli \| bash`** en un home limpio (`/root/trust-agent/.e2e/cli-gov-home`): termina en verde; plugin instalado; binding `org-code.txt`; skin white-label (accent + product del branding); `.env` con `TRUST_AGENT_API`/`TRUST_ORG_CODE`/`TRUST_DEVICE_TYPE=cli`; `trust-login` ejecutable; config del agente (gateway standalone, plugin, toolset, skin, modelo → gateway :4000). |
| login | 5 | Worker PKCE+TOTP real → `id-token.txt` 0600 (el artefacto de `trust-login`); self-issue de la virtual key → **solo `glm-5.2`** (policy aplicada); `model.api_key` en el home. |
| firstrun | 2 | `hermes chat` en el home instalado → auto-enrolamiento zero-UI → device **PENDING con `device_type='cli'`** en BD, sin ningún panel. |
| approve | 5 | Admin aprueba (API spec 007) → el agente canjea el enrollment token → device token; governance bundle **JWS firmado** sincronizado; device ACTIVE conservando `device_type='cli'`. |
| governed | 3 | Modelo APROBADO `glm-5.2` → **200 real** del gateway; modelo NO aprobado `qwen3-32b` → **403 bloqueado** ("not available for this API key"); heartbeat + `applied_policy_version=1` en BD (agente gobernado). |
| revoke | 4 | Admin revoca → heartbeat con el device token → **401** `device_not_authorized`; virtual key bloqueada por el sweep → **401** "Key is blocked" en el gateway; el plugin registra los 401 en `plugin.log`. |

## Archivos

- `01-download-page.html` — página pública con las dos opciones (branding Nimbus).
- `02-cli-installer.sh` — instalador CLI servido (org_code horneado).
- `03-install.log` — salida de `curl | bash` en el home limpio.
- `04-firstrun-pane.txt` — TUI del agente CLI en primera ejecución.
- `05-plugin-log.txt` — log del plugin: auto_enrolled, bundle sync, heartbeat, 401s post-revocación.
- `06-governed-approved-model.json` — 200 real con `glm-5.2`.
- `07-governed-denied-model.json` — 403 real con `qwen3-32b` (no aprobado).
- `08-governed-pane.txt` — TUI del agente gobernado.
- `09-revoked-key-gateway.json` — 401 "Key is blocked" tras la revocación.
- `10-revoke-pane.txt` — TUI tras la revocación.

## Suite automatizada

`platform/tests/test_cli_governed.py` — 10 tests verdes (endpoint CLI,
audit, fail-closed, anti-enumeración, rate limit, página con dos opciones,
`skin.yaml` en el bundle, `device_type` en enroll/list/me/presence, 422 en
valor inválido). Vecinos afectados re-verificados:
`test_hermes_download.py` + `test_devices_flow.py` (28 passed) y paridad
de schema (`db/tests/test_schema_parity.py`, 2 passed).
