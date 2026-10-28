# Policy en vivo + presencia de dispositivos (spec 027)

> Mandato de Miller: las políticas se actualizan desde la organización admin
> y se APLICAN a los Hermes desktops enrolados — y el admin debe poder ver
> CUÁNTOS están conectados y QUIÉNES.

Spec: `specs/027-live-policy-presence/spec.md` · Migración: `0017_live_policy_presence`
· Smoke en vivo: `platform/scripts/smoke_live_policy_presence.py` (21/21 PASS)
· Tests: `platform/tests/test_live_policy_presence.py` (10/10 PASS)

## Cómo funciona

### Presencia (heartbeat)

El plugin `trust-agent-governance` (lane t_c875626f) reporta un heartbeat en
cada ciclo de sync (default 300 s, mismo `TRUST_SYNC_INTERVAL`):

```
POST /api/me/device-heartbeat
Authorization: Bearer <device_token>   (camino plugin; también sesión+MFA)
X-Device-Token: <device_token>
{"applied_policy_version": 3, "uptime_seconds": 3600, "mode": "normal"}

200 → {"current_policy_version": 4, "policy_update_available": true,
       "heartbeat_interval_seconds": 300, "online_window_seconds": 600,
       "server_time": "..."}
```

El control plane persiste `devices.last_heartbeat_at`,
`applied_policy_version` y `agent_uptime_seconds` (migración 0017). Un
dispositivo está **online** mientras su heartbeat sea más joven que la
ventana (default 600 s = 2 intervalos; tolera un heartbeat perdido).
Device revocado → 401 `device_not_authorized` inmediato (fail-closed).

### Propagación de policy en vivo

1. El admin edita una policy (`PUT /api/policies/*`) → la versión vigente
   del usuario/depto sube.
2. El próximo heartbeat del device compara su `applied_policy_version`
   contra la vigente → `policy_update_available: true`.
3. El device re-baja el governance-bundle JWS (`GET
   /api/me/governance-bundle`) en el MISMO ciclo, lo verifica (RS256 pineado,
   `aud`, `exp`, `jti`) y aplica el nuevo efectivo **en el siguiente turno**.
   Sin reiniciar, sin re-enrolar.

**Ventana de consistencia (peor caso): intervalo de heartbeat (300 s
default) + un turno del agente.** Configurable: `TRUST_SYNC_INTERVAL` en el
device; el server comunica `heartbeat_interval_seconds` en la respuesta.

**Fail-closed (decisión D-027-03):** si el refresh del bundle falla, el
bundle anterior VERIFICADO sigue siendo autoridad hasta su `exp` (gracia
offline, D-GOH-04 de spec 019); expirado sin refresh → modo RESTRINGIDO. No
hay deny por "versión desactualizada": una policy anterior firmada y vigente
es autoridad legítima. La revocación dura sí es inmediata (estado del
dispositivo → 401 en el siguiente request).

### Vista de conexiones (Admin Console)

`GET /api/devices/presence` (permiso `devices.manage`, dept-scope para
dept_admin): `online_count`, `active_count` y por dispositivo: usuario,
depto, dispositivo, `last_heartbeat_at`, versión aplicada vs vigente
(`policy_up_to_date`), uptime y estado.

La página **Devices** del Admin Console muestra la franja "X online / Y
active · live" (auto-refresco cada 15 s) y columnas Online / Policy (con
badge "→ vN pending" cuando el device aún no confirmó la versión vigente) /
Last heartbeat.

### Audit (sin flooding)

- `device.heartbeat`: **muestreado** — máximo 1 fila por dispositivo por
  hora (`heartbeat_audit_interval_seconds`, soporte en la columna
  `devices.last_heartbeat_audit_at`).
- `policy.propagated`: exactamente en la transición confirmada (el device
  reporta la versión vigente y la registrada previa era distinta), con
  payload `{from_version, to_version}`.

## Verificación (evidencia real)

- **Smoke en vivo** (`platform/scripts/smoke_live_policy_presence.py`,
  21/21 PASS): control plane real (uvicorn, HTTP real) + flujo de
  enrolamiento REAL (enroll→approve→exchange) → heartbeat → cambio de policy
  por el admin → señal de actualización → re-baja del bundle JWS (nueva
  versión verificada) → confirmación → audit `policy.propagated` →
  presencia (online/quiénes/versión/uptime) → muestreo de audit → revoke →
  401 → offline.
- **Suite** (`platform/tests/test_live_policy_presence.py`, 10/10):
  AC-027-01..07, incluida la propagación con el `BundleSyncer` REAL del
  plugin contra el control plane real sobre HTTP (AC-027-03).
- **Stack live** (:8000): BD en head `0017_live_policy_presence`,
  `POST /api/me/device-heartbeat` y `GET /api/devices/presence` activos
  (401 sin auth), heartbeat real → 200 con presencia persistida y audit
  `device.heartbeat` en la BD live; console :3100 sirve el bundle con la
  franja live (verificado en los chunks servidos).

## Coordinación

- **Plugin (t_c875626f)**: el cliente del heartbeat vive en su plugin —
  contrato completo en `specs/027-live-policy-presence/spec.md` §4 y en el
  comentario seam de su card. El mecanismo de propagación (re-baja del
  bundle) ya existe en su `BundleSyncer` (spec 019 REQ-GOH-004) — verificado
  en vivo por esta spec.
- **Migraciones**: 0017 colgada del head real `0016_provider_superuser`
  (anunciado en el card, regla 9); imagen `migrate` reconstruida y `up -d`
  aplicado (BD live en 0017).
- **Matriz de capacidades (t_d8ed9362)**: sin policy keys nuevas — la
  presencia es infraestructura de gobernanza, no una capacidad del agente.
