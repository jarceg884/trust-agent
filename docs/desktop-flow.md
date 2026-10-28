# Flujo del desktop real — link de descarga → Hermes → enrolamiento automático

> **Estado (t_b8769ecb, mandato Miller, 2026-10-26)**: el desktop-client demo
> (:9090, spec 010) fue **RETIRADO del stack y del repo**. Era un double de
> desarrollo que simulaba el desktop para probar la API de dispositivos —
> nunca fue el producto. El usuario final **NUNCA ve un panel de
> enrolamiento**: cuando usa el link y se autentica, su Hermes queda enrolado
> a la organización automáticamente (zero-UI).
>
> Este documento describe el flujo REAL, implementado por las specs 022
> (puente Hermes real + plugin de gobernanza) y 023 (link de descarga por
> tenant + bundle white-label). Evidencia y runbooks:
> `docs/hermes-download.md` (spec 023) y `docs/hermes-real-bridge.md`
> (spec 022). La versión histórica de este doc (flujo del demo :9090) vive en
> el historial de git.

## 1. Visión general

```
Admin del cliente                Empleado (usuario final)                Control plane
─────────────────                ───────────────────────                ─────────────
Hermes Download (Admin           Abre /download/{slug} → página con
Console, REQ-AC-033)   ───────►  branding del tenant → descarga el
comparte el link                 tarball (bundle real de Hermes)
                                                                        GET /download/{slug}/bundle
                                                                        audit download.issued
                                 install.sh → instala Hermes COMPLETO
                                 en su home: plugin trust-agent-
                                 governance + binding de org + skin
                                 white-label + gateway.standalone
                                 trust-login → navegador real: SSO+MFA
                                 del realm (acr=2, PKCE S256,
                                 callback 127.0.0.1:8765)       ─────►  mint de sesión + virtual key
                                 AUTO-ENROLL zero-UI (plugin):   ─────►  POST /api/devices/enroll → 202 pending
                                 submit + poll, SIN paneles              enrollment_token one-time
                                 (el plugin lo hace solo)        ◄────►  POST /api/devices/enroll/exchange
Aprueba el device desde el                                               (202 pending / 200 token / 410)
Admin Console (control de        Device token 0600 en el home   ◄──────  200 {device_token} al aprobar
seguridad, D-023-03)             Sesión GOBERNADA: el Hermes del
                                 empleado corre bajo la policy
                                 firmada de la org (veto de tools,
                                 modelos/presupuesto vía LiteLLM,
                                 heartbeat + attestation)
```

Tres momentos, **cero UI de enrolamiento** para el invitado:

1. **Link** — el admin del cliente genera/comparte el link de descarga desde
   SU Admin Console (sección "Hermes Download"). El proveedor NO genera links
   (corrección Miller t_360a752e).
2. **Instalación + login** — el empleado descarga el bundle y corre
   `install.sh`; luego `trust-login` hace el baile OIDC con el navegador real.
3. **Enrolamiento automático + aprobación** — el plugin enrola el dispositivo
   por sí mismo (submit + poll); el admin de la org aprueba desde el Admin
   Console. La aprobación SE MANTIENE como control de seguridad (D-023-03):
   con `org_code` público, auto-aprobar convertiría el link en enrolamiento
   sin control.

## 2. Link de descarga por tenant (spec 023)

| Pieza | Detalle |
|---|---|
| Página pública | `GET /download/{tenant_slug}` — HTML con branding publicado del tenant, rate-limited (contadores en PostgreSQL), anti-enumeración (404×5 → 429) |
| Bundle | `GET /download/{slug}/bundle` — tarball REAL: plugin `trust-agent-governance` (spec 022) + `install.sh` + `trust-login` + manifest con `org_code` y sha256 por miembro |
| Gestión del link | `GET/PUT /api/download-link` (contrato `{url, enabled, updated_at}`; permiso `download_link.manage`, solo org_admin; toggle en el Admin Console) |
| Audit | `download.link.enabled` / `download.link.disabled` / `download.issued` (payload `{slug, ip, user_agent, bundle_sha256}`) |
| Link deshabilitado | descarga → 403 `link_disabled` |

## 3. Instalación (install.sh del bundle)

Instala **Hermes completo** en el home del usuario (`$HERMES_HOME` o
`~/.hermes`), sin parches al core (constitution IV, lección C6):

- plugin `trust-agent-governance` activo (`plugins.enabled`);
- `gateway.standalone true` — gateway dedicado por perfil (hallazgo crítico
  spec 022: el gateway multiplex corre los turnos contra el scope default y
  los hooks del plugin no disparan);
- binding de org (`org-code.txt`) y skin white-label derivado del branding
  publicado del tenant;
- `platform_toolsets.cli` con la tool `governance_status`;
- `.env` del perfil con `TRUST_AGENT_API` y la virtual key (se escribe una
  sola vez; el key material se muestra exactamente una vez).

## 4. Login — trust-login (OIDC authorization code + PKCE S256)

El helper `trust-login` del bundle abre el **navegador real** del usuario
contra el realm de su organización (`{idp}/realms/{org_code}`):

- cliente público `trust-agent-web`, `acr_values=2` (SSO+MFA — puerta MFA:
  `amr` O `acr ≥ 2`, spec 011 D-MFA-01);
- callback local `http://127.0.0.1:8765/callback` (registrado en el cliente
  público de cada realm por el provisioning; `ensure_web_client_redirect_uris`
  lo cura en realms pre-existentes);
- el `id_token` verificado queda en `id-token.txt` con permisos 0600; la
  virtual key del usuario se auto-emite (self-issue) contra el control plane.

## 5. Enrolamiento automático zero-UI (plugin, spec 007/022)

Primera clase, **cero scraping** (D-DEV-01): la identidad del dispositivo la
aporta el propio dispositivo. El plugin lo hace TODO sin paneles:

1. **Keypair local** (RSA-2048): la pública viaja como SPKI PEM; la privada
   (PKCS#8) **nunca sale del dispositivo** (keystore del home, 0600).
2. `POST /api/devices/enroll` con el `id_token` MFA-verified → **202**
   `{device_id, status:"pending", enrollment_token}` — one-time
   (`enr1.<tenant>.<device>.<secret>`; en DB solo su sha256).
3. **Polling** `POST /api/devices/enroll/exchange`:
   - `pending` → 202 (no consume) — sigue esperando;
   - admin aprueba (Admin Console, `POST /api/devices/{id}/approve`) →
     **200** `{device_token, expires_in}` (consume el hash);
   - rechazado/revocado/consumido → **410** uniforme, sin oráculo.
4. Audit: `device.enrolled` (usuario) y `device.approved` (admin) con
   `device_id` (REQ-DEV-006).

El device token JWT (HS256, TTL 15 min, renovable vía
`POST /api/devices/{id}/token`) pasa el `DeviceGate`, alimenta el heartbeat
del plugin y su revocación invalida el acceso **en la misma transacción**
(D-DEV-07, spec 019: además barre conectores y bloquea la virtual key).

## 6. Sesión gobernada — el Hermes del empleado queda gobernado

Lo que la organización impone (no solo muestra):

- **Tools**: veto `pre_tool_call` por `allowed_tools` de la policy firmada
  (mensaje exacto `BLOCKED by organization policy: tool <t> is not allowed.
  Allowed tools: …`); fail-closed en modo `restricted` (toda tool externa
  bloqueada si la policy no se puede refrescar).
- **Modelos y presupuesto**: el tráfico LLM pasa por el gateway LiteLLM del
  tenant con la virtual key del usuario (D-GOV-15) — la key rechaza modelos
  no aprobados y aplica `max_budget`. Sin `fallback_model`: nada evade el
  gateway.
- **Policy en vivo**: heartbeat del plugin aplica la policy vigente
  (v1→v2→v3 en el mismo ciclo, sin reinicio) y reporta attestation
  (uptime, versión del plugin, vetos) — `devices.last_heartbeat_at`,
  `applied_policy_version`, `agent_uptime_seconds` en BD.
- **Visibilidad para el usuario**: la tool `governance_status` del plugin
  muestra su estado gobernado (presupuesto, modelos, tools permitidas) —
  no existe un dashboard web del empleado.

## 7. Artefactos (repo)

| Artefacto | Ruta |
|---|---|
| Plugin `trust-agent-governance` (núcleo v2 + enroll + enforcement + attestation) | `platform/hermes/plugins/trust-agent-governance/` |
| Plantilla de perfil (config + SOUL) | `platform/hermes/profile-template/` |
| Empaquetado del bundle (servidor) | `platform/app/download/` (page, bundle, router) |
| Harness E2E (8 fases, idempotente) | `platform/scripts/evidence_hermes_download.py` |
| Tests | `platform/tests/test_hermes_download.py`, `test_hrb_enforcement.py`, `test_hrb_heartbeat.py`, `test_governance_bundle.py`, `test_governance_attestation.py`, `test_live_policy_presence.py` |

El Dockerfile del API copia el plugin a `/opt/trust-agent/hermes-plugins`
(`settings.hermes_plugin_dir`) para empaquetarlo en el tarball.

## 8. Cómo verificarlo (runbook)

```bash
cd /root/trust-agent
docker compose up -d --remove-orphans   # stack completo (SIN desktop-client)

# E2E completo link→bundle→install→login→auto-enroll→approve→gobernanza:
cd platform && .venv/bin/python scripts/evidence_hermes_download.py --phase all
# Runbook detallado: docs/hermes-download.md §5
# Puente Hermes real + policy en vivo: docs/hermes-real-bridge.md §10
```

## 9. Historia: el demo :9090 (spec 010) y el hallazgo KC26

El desktop-client Next.js (:9090) fue el double de desarrollo de la spec 010
(2026-10-23): simulaba login+enrolamiento+dashboard para ejercitar la API de
dispositivos contra Keycloak real. Cumplió su propósito (contratos spec 007
verificados, hallazgo KC26 `amr`/`acr` → spec 011 D-MFA-01, skill
`identity/keycloak26-amr-mfa`) y fue retirado cuando el flujo real convergió
(specs 022/023). Su código y la versión original de este documento quedan en
el historial de git.
