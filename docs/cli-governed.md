# Hermes CLI gobernado — link de instalación ligado a la organización (spec 034)

> Mandato de Miller: además del desktop, el admin debe poder pasar un link
> para tener **Hermes CLI gobernado** — el empleado que trabaja en un
> servidor/VPS/terminal instala el Hermes CLI ya ligado a la organización,
> con las mismas policies. Spec: `specs/034-cli-governed/`.

## El flujo

```
Admin (Admin Console → Hermes Download)
  └─ comparte:  curl -fsSL https://<control-plane>/download/<org_code>/cli | bash
Empleado (Linux / macOS / WSL2)
  ├─ 1. curl | bash  → instala Hermes CLI (instalador oficial si falta) +
  │                     plugin trust-agent-governance + binding <org_code> +
  │                     skin white-label + gateway del tenant
  ├─ 2. trust-login   → OIDC authorization-code + PKCE (SSO + MFA en el
  │                     navegador) → id-token.txt + virtual key one-time
  └─ 3. hermes        → auto-enrolamiento zero-UI: device PENDING con
                        device_type='cli' → el admin lo aprueba → device
                        token + governance bundle JWS → el agente corre
                        bajo las policies/budgets/audit de la org
```

## Endpoints y artefactos

| Pieza | Detalle |
|---|---|
| `GET /download/{slug}/cli` | Instalador bash por tenant (público, igual que el bundle). Hornea `org_code`, `api_base`, gateway LiteLLM y modelo del tenant. Headers `X-Installer-SHA256`. Audit `download.issued` con `payload.artifact="cli"`. |
| `GET /download/{slug}` | Página pública con **dos opciones**: "Download for desktop" (bundle spec 023) y "Install on a server (CLI)" — comando `curl` copiable + requisitos Linux/macOS/WSL2. Branding del tenant en ambas. |
| Bundle canónico | El instalador CLI **no duplica** el plugin: descarga `GET /download/{slug}/bundle` del mismo origen, verifica el `X-Bundle-SHA256` y extrae `plugin/trust-agent-governance/`, `trust-login` y `skin.yaml` (miembro nuevo del tarball, D-034-02). |
| `devices.device_type` | Columna nueva (migración `0025_device_type`): `'cli'` \| `'desktop'` (default retrocompatible). El plugin la envía en `POST /api/devices/enroll` desde el env `TRUST_DEVICE_TYPE` que el instalador deja en `$HERMES_HOME/.env`. Visible en Devices (badge CLI/Desktop) y presence. |
| Admin Console | "Hermes Download" muestra el link desktop (copiable) y el comando CLI (copiable); el mensaje de invitación incluye ambos. |

## Seguridad (idéntica al desktop)

- `org_code` es un identificador **público** (D-023-02): la seguridad del
  enrolamiento es la aprobación admin (spec 007), no el link.
- Rate limit positivo por IP, anti-enumeración (sondeo de slugs quema el
  contador), fail-closed: tenant suspendido → 403, link deshabilitado →
  403 `link_disabled` (afecta a página, bundle e instalador CLI).
- Cero secretos en el instalador: el `id_token` lo produce `trust-login`
  (OIDC code+PKCE + MFA, nunca passwords en la terminal) y la virtual key
  se auto-emite one-time (`--issue-key`).
- Cero parches al core de Hermes: el plugin existente + empaquetado.
- Enrolamiento idéntico: pending → approve → device token → governance
  bundle JWS firmado (spec 019) → heartbeat/presence (spec 027) →
  revocación integral (spec 019 Inc. 4: sessions + virtual keys +
  connectors en la misma transacción).

## Enforcement de modelos (lo que ve el empleado)

La virtual key del usuario se emite con `models = aprobados ∩ policy
allowed_models` (spec 018/032). El gateway LiteLLM real rechaza cualquier
modelo fuera de esa lista — evidencia `docs/evidencia/cli-governed/`:
`glm-5.2` (aprobado) → 200 real; `qwen3-32b` (no aprobado) → 403
"not available for this API key". Tras revocar el device, la key queda
bloqueada (401 "Key is blocked") y el device token responde 401
(`device_not_authorized`).

## Nota operativa: prompt de telemetría

El instalador fija `telemetry.shared_metrics.enabled=false`, pero el TUI
de Hermes re-pregunta el opt-in una vez por home fresco ("Asking once
more…"). Es comportamiento del core (no se parchea, constitution IV): el
empleado pulsa Enter una vez en el primer arranque. El harness de evidencia
lo descarta automáticamente.

## Evidencia

`docs/evidencia/cli-governed/` — corrida live 2026-10-27 contra el stack
real (api :8000, Keycloak :8080, LiteLLM real :4000): **51 checks PASS,
0 FAIL**. Script: `platform/scripts/evidence_cli_governed.py`
(`--phase setup|download|install|login|firstrun|approve|governed|revoke`).

## Archivos principales

- `platform/app/download/cli.py` — builder del instalador CLI por tenant.
- `platform/app/download/router.py` — `GET /download/{slug}/cli` (+ audit).
- `platform/app/download/page.py` — página pública con las dos opciones.
- `platform/app/download/bundle.py` — miembro `skin.yaml`.
- `platform/app/devices/{router,service}.py` — `device_type`.
- `db/migrations/versions/20261027_0025_device_type.py` (+ `db/schema.sql`).
- `platform/hermes/plugins/trust-agent-governance/enroll.py` — envía
  `device_type` (env `TRUST_DEVICE_TYPE`).
- `platform/console/src/app/(console)/hermes-download/*` y `devices/*` — UI.
- `platform/tests/test_cli_governed.py` — suite de la spec (10 tests).
