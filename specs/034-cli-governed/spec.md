# Spec 034 — Hermes CLI gobernado: link de instalación ligado a la org (mandato Miller)

> **Nota de numeración**: el mandato de la tarea refería `spec 029-cli-governed/`,
> pero `specs/029-policy-catalog-v2/` ya existe (spec 029 tomada). Esta feature
> vive en `specs/034-cli-governed/` — el siguiente número libre. Razón
> documentada aquí per constitution II.

## 1. Problema

El bundle desktop (spec 023) cubre al empleado con workstation, pero el
mandato de Miller exige también al empleado que trabaja en un
**servidor/VPS/terminal**: el admin comparte un link y el empleado instala el
**Hermes CLI ya ligado a la organización** con `curl -fsSL <link> | bash`,
quedando bajo las mismas policies, budget, audit y enrolamiento que el
desktop. El plugin `trust-agent-governance` es del core de Hermes y funciona
idéntico en CLI y desktop: lo que falta es el **artefacto** (instalador CLI
por tenant) y el **flujo** (página con dos opciones, tipo de dispositivo
distinguible, consola con ambos links, E2E).

## 2. Decisiones (D-034-XX)

- **D-034-01 — Endpoint**: `GET /download/{slug}/cli` sirve el instalador
  bash por tenant (mismo endpoint público de descarga, spec 023). Mismo
  rate limit, anti-enumeración, fail-closed (suspendido/disabled) y audit
  `download.issued` con `payload.artifact = "cli"` que el bundle
  (`"bundle"`). `org_code` embebido — público (D-023-02); la seguridad
  sigue siendo la aprobación admin (spec 007).
- **D-034-02 — Fuente única del plugin**: el instalador CLI NO duplica el
  árbol del plugin: descarga el bundle canónico
  `GET /download/{slug}/bundle` del mismo origen y extrae
  `plugin/trust-agent-governance/` + `trust-login` + `skin.yaml`. El bundle
  gana el miembro `{root}/skin.yaml` (empaquetado; retrocompatible — el
  `install.sh` del desktop sigue horneando el skin inline).
- **D-034-03 — Targets**: Linux/macOS/WSL2 (targets oficiales del CLI
  según la doc de Hermes). El script exige `bash`+`curl`+`tar`, instala
  Hermes vía el instalador oficial si no está presente (igual que el
  desktop, D-023-04) y acepta `--home DIR` (HERMES_HOME).
- **D-034-04 — device_type**: nueva columna `devices.device_type text NOT
  NULL DEFAULT 'desktop' CHECK (device_type IN ('desktop','cli'))`
  (migración Alembic 0025 vía migration_guard, paridad schema.sql §5.9).
  El plugin envía `device_type` en `POST /api/devices/enroll` leído del
  env `TRUST_DEVICE_TYPE` (default `desktop`); el instalador CLI lo deja
  en `$HERMES_HOME/.env` como `cli`. Server valida el valor (422 si no es
  `desktop|cli`). Cero parches al core de Hermes: el cambio es en NUESTRO
  plugin (empaquetado + un campo del payload de enrolamiento).
- **D-034-05 — Enrolamiento idéntico**: misma API spec 007 (pending →
  approve → token), mismo governance-bundle JWS (spec 019), mismas
  policies/budget/audit. Un dispositivo CLI es indistinguible de un
  desktop salvo por `device_type` (visible en Devices y presence).
- **D-034-06 — Página pública**: `/download/{slug}` ofrece DOS opciones
  con branding del tenant: "Desktop app" (bundle existente) y "CLI"
  (comando `curl -fsSL <origin>/download/{slug}/cli | bash` copiable +
  instrucciones Linux/macOS/WSL2). El origin es `request.base_url`
  (D-023-01/06, mismo criterio que el binding del bundle).
- **D-034-07 — Admin Console**: la sección Hermes Download muestra el link
  desktop (existente) y el comando CLI (`curl -fsSL {url}/cli | bash`),
  ambos con copiar al portapapeles. El comando CLI se construye desde la
  MISMA `url` del contrato `GET /api/download-link` (sin cambio de
  contrato). La vista Devices muestra el tipo (CLI / Desktop).
- **D-034-08 — Sin secretos**: el instalador CLI no embebe credenciales;
  el id_token lo produce `trust-login` (OIDC code+PKCE, ya en el bundle) y
  la virtual key se auto-emite con `--issue-key`. Idéntico al desktop.

## 3. Requisitos (REQ-034-XXX)

- **REQ-034-001** Instalador CLI por tenant: `GET /download/{slug}/cli`
  responde `200 text/x-shellscript` con un script bash ejecutable que
  hornea `org_code`, `api_base`, `litellm_base` y `model` del tenant;
  instala Hermes (si falta) + plugin + binding + skin + config
  (`gateway.standalone`, `plugins.enabled`, `platform_toolsets.cli`,
  `display.skin`, `model.*`) + `TRUST_DEVICE_TYPE=cli` en `.env`.
- **REQ-034-002** Seguridad del endpoint: rate limit positivo, slug
  desconocido → 404 + quema de contador IP, tenant suspendido → 403
  fail-closed, link deshabilitado → 403 `link_disabled`, audit
  `download.issued` (+1 por servido, `artifact:"cli"`).
- **REQ-034-003** Página pública con las dos opciones y branding (D-034-06).
- **REQ-034-004** Enrolamiento con `device_type`: `POST /api/devices/enroll`
  acepta `device_type` opcional (`desktop|cli`, default `desktop`, 422 en
  valor inválido); persiste en `devices.device_type`; `GET /api/devices`,
  `GET /api/devices/{id}`, `GET /api/devices/me` y `GET /api/devices/presence`
  lo exponen.
- **REQ-034-005** Admin Console: comando CLI copiable en Hermes Download;
  badge de tipo en Devices.
- **REQ-034-006** E2E real en entorno limpio: `curl -fsSL <link>/cli | bash`
  con `HERMES_HOME` temporal → primera ejecución → device **pending con
  `device_type='cli'`** → admin approve → device token + bundle JWS →
  policy aplicada (modelo no aprobado → bloqueado por el gateway con la
  virtual key del usuario) → revoke → 401 con el device token. Evidencia
  en `docs/evidencia/cli-governed/`.

## 4. Fuera de alcance

- Cambios al core de Hermes (prohibido, constitution IV).
- Windows nativo (los targets oficiales del CLI son Linux/macOS/WSL2).
- Nuevo flujo de enrolamiento o de firma del bundle (se reusan spec 007/019).
