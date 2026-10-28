# Hermes Download por tenant — evidencia E2E (spec 023, t_526030f3)

**Estado: CONVERGIDA** — implementación == spec 023; todos los AC-HDL con
evidencia real en vivo contra el stack completo (api :8000, Keycloak real
:8080, LiteLLM real :4000, PostgreSQL :55432). Corrida final: 2026-10-26
(bundle final, con telemetría off en el instalador).

El seam link→app quedó cerrado: el **admin del cliente** comparte el link
estable → un **empleado con navegador anónimo** descarga el bundle →
`install.sh` instala un **Hermes COMPLETO** con plugin + binding + skin
white-label → `trust-login` (OIDC code+PKCE, navegador real) → **primera
ejecución: auto-enroll zero-UI** → el admin aprueba → **el agente queda
gobernado por la policy de la org** (veto exacto + heartbeat + attestation).

## 1. Qué se construyó

| Pieza | Archivo |
|---|---|
| Endpoint público `GET /download/{slug}` (página HTML con branding) + `GET /download/{slug}/bundle` | `platform/app/download/router.py`, `page.py` |
| Bundle real in-memory (plugin spec 022 + install.sh + trust-login + README + manifest con sha256 por miembro) | `platform/app/download/bundle.py` |
| Gestión del link `GET/PUT /api/download-link` (contrato EXACTO con la UI del Admin Console, permiso nuevo `download_link.manage` solo org_admin) | `platform/app/download/router.py` |
| Rate limit positivo reutilizando `ops.login_throttle` (D-023-07) | `platform/app/auth/throttle.py::register_hit` |
| Migración 0021 (tenants.download_enabled/updated_at + permiso + acciones de audit) | `db/migrations/versions/20261026_0021_hermes_download.py` + paridad `db/schema.sql` §17 |
| Redirect URI del helper CLI en el cliente público de cada realm | `config.py` (`oidc_redirect_uris`) + `docker-compose.yml` |
| Tests (página, bundle, audit, rate limit, anti-enumeración, toggle, permisos, aislamiento, suspendido) | `platform/tests/test_hermes_download.py` — **9/9**, suite completa **512 passed** |
| Harness E2E re-ejecutable | `platform/scripts/evidence_hermes_download.py` |

## 2. Criterios de aceptación → evidencia

| AC | Criterio | Evidencia (corrida final, tenant real `hermes-dl-e2e` / "Aurora Corp") |
|---|---|---|
| **AC-HDL-001** | Navegador anónimo abre el link → página 200 con branding real | `evidencia/hermes-download/00-download-page-screenshot.png` + `01-download-page.html` — "Aurora Agent" + accent `#0ea5e9` inline, botón al bundle. Fase download: **D1 ×3 PASS** |
| **AC-HDL-002** | Descarga entrega tarball real con binding | Fase download **D2 ×6 PASS**: `tar -tzf` lista plugin/install.sh/trust-login/README/manifest; `manifest.json` lleva `org_code=hermes-dl-e2e`; header `X-Bundle-SHA256` = sha256 real |
| **AC-HDL-003** | Instalación real en home fresco | Fase install **I1-I3 11/11 PASS**: `org-code.txt=hermes-dl-e2e`, plugin instalado, skin `trust-hermes-dl-e2e.yaml` con accent + product_name, `.env` con `TRUST_AGENT_API`/`TRUST_ORG_CODE`, config: `gateway.standalone=true`, plugin enabled, toolset cli, skin activo, model → gateway del tenant (`02-install.log`) |
| **AC-HDL-004** | Audit `download.issued` por descarga | **D3 PASS**: `audit_log` del tenant `download.issued` 2→3 con payload `{slug, ip, user_agent, bundle_sha256}` |
| **AC-HDL-005** | Toggle enable/disable del admin + efecto público | **D4 ×6 PASS**: `GET /api/download-link` → contrato `{url, enabled, updated_at}` exacto; `PUT enabled=false` → audit `download.link.disabled` → bundle 403 `link_disabled` + página disabled honesta; re-enable restaura |
| **AC-HDL-006** | Rate limit | **D6 PASS**: ráfaga misma IP → `[200, 200, 429, ...]` con `Retry-After` (contadores en PostgreSQL, sobreviven restarts) |
| **AC-HDL-007** | Anti-enumeración | **D5 PASS**: slug desconocido → 404 ×5, 6º sondeo → 429 (mismo contador IP que `/api/auth/org-config`) |
| **AC-HDL-008** | Login real + auto-enroll zero-UI | `07-trust-login.log`: navegador real completa SSO+MFA (acr=2) contra el realm real → `id-token.txt` 0600 + virtual key self-emitida one-time y guardada con `hermes config set`. Fase login-verify **L1-L2 4/4**. Primera ejecución: device **pending en BD sin ningún panel** (`auto_enroll_attempt outcome=pending`, pid del chat) → admin approve → **`auto_enrolled`** con device token (`03-plugin-log.txt`) |
| **AC-HDL-009** | Agente gobernado por la policy de la org | Fase governed **G1-G3 5/5 PASS**: transcripción canónica con el mensaje EXACTO `{"error": "BLOCKED by organization policy: tool 'terminal' is not allowed. Allowed tools: governance_status, web_search."}`; `plugin.log` con `veto_eval` + `tool_vetoed`; `bundle_synced`/`heartbeat_ok`/`attestation_sent` con `policy_version=4`; BD: device ACTIVE con `applied_policy_version=4` y heartbeat (`05-governed-pane.txt`) |
| **AC-HDL-010** | Aislamiento + suite en verde | `test_hermes_download.py` 9/9 (página de A sin datos de B, bundles por tenant, permisos, suspendido); suite completa **512 passed** |

## 3. El flujo E2E completo (corrida final)

```
[provider]  POST /api/admin/tenants → tenant hermes-dl-e2e ("Aurora Corp")
[admin]     PKCE+TOTP real (acr=2) → policy allowed_tools=[web_search,governance_status]
            → budget $20 → branding "Aurora Agent"/#0ea5e9 publicado (v1)
[admin]     comparte el link estable: http://127.0.0.1:8000/download/hermes-dl-e2e
[empleado]  navegador ANÓNIMO → página con branding → descarga el tarball
            (audit download.issued; X-Bundle-SHA256 verificado)
[empleado]  ./install.sh --home <fresco> → Hermes COMPLETO + plugin + binding + skin
[empleado]  ./trust-login --issue-key → navegador real SSO+MFA → id-token.txt 0600
            + virtual key one-time → hermes config set model.api_key
[empleado]  hermes → auto-enroll zero-UI → device PENDING (sin paneles)
[admin]     POST /api/devices/{id}/approve → active
[agente]    canjea el token (auto_enrolled) → bundle firmado v4 → heartbeat
[empleado]  "use the terminal tool" → VETO exacto por policy → el agente se lo
            comunica y no ejecuta nada fuera de allowed_tools
```

## 4. Hallazgos reales (documentados para las lanes correspondientes)

1. **[MEDIO] Race del auto-enroll multi-proceso (plugin, spec 022 — lane
   t_c875626f)**: con `gateway.standalone:true`, un `hermes chat` arrastra
   DOS procesos con el plugin (gateway standalone + TUI). Si además se
   arranca un gateway explícito, los loops de auto-enroll de ambos procesos
   se pisan el enrollment token one-time (cada `POST /enroll` resetea el
   hash del token del device pending) → 410s mutuos. **Converge** tras la
   aprobación (el último token guardado coincide con el hash final), pero
   genera ruido y un canje fallido temporal. Reportado como tarea de
   follow-up. El instalador de esta spec NO arranca gateways explícitos
   (solo `hermes chat`; el standalone se auto-spawnea).
   **FIX (t_dd2f5f65)**: `enroll.py` ahora serializa el enroll entre
   procesos del mismo home (flock sobre `$HERMES_HOME/trust-agent/
   .autoenroll.lock`) y reutiliza el enrollment token guardado antes de
   re-POSTear (reuse-first) — un solo `POST /enroll`, cero 410s mutuos;
   regresión cubierta en `tests/test_hrb_enforcement.py`
   (`test_auto_enroll_multiprocess_no_mutual_410`, 3 procesos reales).
2. **TUI fresco + send-keys**: el primer `send-keys` con texto+Enter juntos
   no envía el mensaje (queda en el buffer) — el harness separa texto y Enter.
3. **Realm LoA condicional (spec 015)**: sin `acr_values=2` el realm emite
   `acr=1` SIN MFA aunque el usuario tenga TOTP. `trust-login` siempre pide
   `acr_values=2` (D-023-13); los bailes del harness también.
4. **Redirect URI del CLI**: el callback `http://127.0.0.1:8765/callback`
   debe estar en `TRUST_OIDC_REDIRECT_URIS` (compose) — el provisioning lo
   registra por realm y `ensure_web_client_redirect_uris` cura realms
   pre-existentes (ver §6 paso 0b).

## 5. Cómo reproducir (runbook)

```bash
cd /root/trust-agent/platform
.venv/bin/python scripts/evidence_hermes_download.py --phase setup      # tenant+MFA+policy+branding
.venv/bin/python scripts/evidence_hermes_download.py --phase download   # página+bundle+audit+rate limit
.venv/bin/python scripts/evidence_hermes_download.py --phase install    # home fresco del empleado
# Paso humano asistido (navegador real del empleado):
env HERMES_HOME=/root/trust-agent/.e2e/hermes-dl-home \
  /root/trust-agent/.e2e/dl-bundle/pkg/trust-agent-hermes-dl-e2e/trust-login --issue-key
#   → completar SSO+MFA en el navegador (credenciales en --phase login-prep)
.venv/bin/python scripts/evidence_hermes_download.py --phase login-verify
.venv/bin/python scripts/evidence_hermes_download.py --phase firstrun    # auto-enroll → pending
.venv/bin/python scripts/evidence_hermes_download.py --phase approve     # admin aprueba
.venv/bin/python scripts/evidence_hermes_download.py --phase governed    # veto + heartbeat
```

Notas de re-ejecución:
- El setup es idempotente (tenant existente se reutiliza; TOTP se re-enrola).
- `firstrun`/`approve`/`governed` operan sobre el device NUEVO de la corrida
  (los devices de corridas previas quedan en BD como histórico).
- Antes de re-instalar en el mismo home, detener gateways residuales:
  `hermes gateway stop` con el `HERMES_HOME` correspondiente (un gateway
  viejo adueñado del home bloquea el arranque del chat nuevo).
- Si el realm pre-existe sin el callback del CLI (paso 0b):
  `docker exec trust-agent-api python -c "from app.tenants.keycloak_admin import KeycloakAdminClient; KeycloakAdminClient().ensure_web_client_redirect_uris('hermes-dl-e2e')"`

## 6. Artefactos

- Evidencia cruda: `docs/evidencia/hermes-download/` (screenshot, HTML de la
  página, install.log, plugin.log, panes del TUI, salida de trust-login).
- Spec: `specs/023-hermes-download/` (spec.md CONVERGIDA, plan.md, tasks.md).
- Home del empleado E2E: `/root/trust-agent/.e2e/hermes-dl-home` (inspeccionable).
- Bundle descargado: `/root/trust-agent/.e2e/dl-bundle/trust-agent-hermes-dl-e2e.tar.gz`.
