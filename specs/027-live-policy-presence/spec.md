# Spec 027 — Policy en vivo + presencia de dispositivos (mandato Miller)

> Mandato: deben existir políticas de qué quiero que haga un usuario o no —
> actualizables desde la organización admin — y APLICADAS a los Hermes
> desktops que mantienen esa política. Por ende el admin debe poder ver
> CUÁNTOS están conectados y QUIÉNES.

## 1. Problema

Las policies por nivel (org→depto→usuario, deny-overrides) se editan desde el
Admin Console y el governance-bundle JWS versionado existe (spec 019), pero:

1. El control plane no sabe qué dispositivos están conectados AHORA: la única
   señal es `devices.last_seen_at` (throttle 60 s del DeviceGate), que no
   distingue "sync de arranque" de "agente vivo reportando", no lleva la
   versión de policy aplicada ni el uptime del agente.
2. La propagación de un cambio de policy depende del ciclo de sync del bundle
   sin señal explícita: no hay contrato de "tu versión está desactualizada,
> re-baja ahora", ni auditoría de cuándo un dispositivo CONFIRMÓ la nueva
   versión.
3. El Admin Console no muestra presencia en vivo (cuántos/quiénes).

## 2. Alcance

- **Control plane (esta lane)**: endpoint de heartbeat, registro de presencia
  por dispositivo, señal de actualización de policy, endpoint de presencia
  para el console, audit muestreado + `policy.propagated`, migración 0015.
- **Plugin trust-agent (lane t_c875626f, seam comentado en su card)**: el
  cliente que reporta el heartbeat en cada ciclo de sync y re-baja el bundle
  cuando el control plane lo indica. El contrato está fijado aquí (§4); la
  implementación del cliente es de esa lane. La propagación en vivo se apoya
  en el mecanismo de sync YA existente del plugin (spec 019 REQ-GOH-004), por
  lo que esta spec la verifica E2E con el `BundleSyncer` real.
- **Admin Console**: sección de conexiones en vivo en Devices (poll 15 s).

Fuera de alcance: WebSocket/SSE (poll es suficiente y es el estándar UI Pro
t_7cd4f40e para tablas administrativas), attestation completa (Incremento 3
de spec 019), push inmediato de policy (sin heartbeat no hay canal).

## 3. Decisiones

- **D-027-01 — Contrato del heartbeat.** `POST /api/me/device-heartbeat`,
  auth idéntica a `GET /api/me/governance-bundle` (`require_bundle_auth`:
  Bearer device token + X-Device-Token; el camino sesión+MFA también vale).
  Request: `{"applied_policy_version": int|null, "uptime_seconds": int>=0,
  "mode": "normal"|"restricted"}`. Response 200: `{"device_id",
  "current_policy_version", "policy_update_available": bool,
  "heartbeat_interval_seconds", "online_window_seconds", "server_time"}`.
  **Amend (spec 022 v1.1.0, REQ-HRB-010)**: cuando la auth llegó por el
  camino plugin (Bearer = device token), la respuesta añade
  `device_token` (token FRESCO, mismo `device_id`/`user_id`/`tenant_id`,
  TTL vigente) + `expires_in` — sliding renewal emitido en la MISMA
  transacción que ejecutó el DeviceGate (revocado → 401 ANTES de emitir;
  la renovación jamás relaja la revocación). El camino sesión+MFA NO
  renueva (el desktop tiene `POST /api/devices/{id}/token`).
  Revocado → 401 `device_not_authorized` (DeviceGate en la misma tx).

- **D-027-02 — Intervalo y ventana online.** El heartbeat viaja en el mismo
  ciclo de sync del plugin: intervalo por defecto 300 s (`TRUST_SYNC_INTERVAL`
  existente). El server lo comunica en la respuesta
  (`heartbeat_interval_seconds`) para que el plugin se autoajuste. Un
  dispositivo está **online** si `last_heartbeat_at >= now() - ventana`, con
  ventana por defecto 600 s (2 intervalos: tolera un heartbeat perdido).
  Setting: `presence_online_window_seconds` (default 600, piso 60).

- **D-027-03 — Propagación en vivo y fail-closed.** El heartbeat compara el
  `applied_policy_version` reportado contra `current_policy_version` (máx
  versión activa combinada org+depto+usuario — misma función que firma el
  bundle). Si difiere → `policy_update_available: true` y el dispositivo
  re-baja el governance-bundle JWS en el mismo ciclo; el nuevo efectivo rige
  desde el siguiente turno. **Ventana de consistencia peor caso = intervalo
  de heartbeat (300 s default).** Fail-closed si el refresh falla: el bundle
  anterior VERIFICADO sigue siendo autoridad hasta su `exp` (gracia offline,
  D-GOH-04); expirado sin refresh → RESTRINGIDO (spec 019 §5.4). NO se exige
  deny por "versión desactualizada": una policy anterior firmada y vigente es
  autoridad legítima; la revocación dura sigue siendo inmediata por estado
  del dispositivo (REQ-GOH-010).

- **D-027-04 — Presencia.** `GET /api/devices/presence` (permiso
  `devices.manage`, dept-scope igual que `GET /api/devices`): recuento
  (`online_count`, `active_count`) + por dispositivo: usuario, depto,
  dispositivo, `last_heartbeat_at`, `applied_policy_version`,
  `current_policy_version`, `policy_up_to_date`, `online`, `agent_uptime_seconds`,
  `status`. `online` solo se computa sobre dispositivos `active`.

- **D-027-05 — Audit sin flooding.** `device.heartbeat`: MÁXIMO una fila por
  dispositivo cada `heartbeat_audit_interval_seconds` (default 3600; columna
  `devices.last_heartbeat_audit_at` lleva el muestreo). `policy.propagated`:
  exactamente en la transición confirmada — un heartbeat reporta
  `applied_policy_version == current` y el valor registrado previo en la fila
  era distinto (payload `{from_version, to_version}`). Ambas acciones se
  siembran en `catalog.audit_actions` (migración 0015, paridad schema.sql).

- **D-027-06 — Columnas.** `devices` += `last_heartbeat_at timestamptz`,
  `applied_policy_version integer`, `agent_uptime_seconds integer`,
  `last_heartbeat_audit_at timestamptz`. `last_seen_at` existente NO se toca
  (sigue siendo la señal del DeviceGate, D-DEV-09).

- **D-027-07 — UI en vivo.** La página Devices del Admin Console gana una
  franja "Live connections" con el contador online/active y auto-refresco
  (poll 15 s, sin CLS, skeletons existentes), columnas nuevas: Online (badge),
  Policy (aplicada vs actual, con indicador "update pending" cuando
  `applied < current`), Last heartbeat. Estándar UI Pro (t_7cd4f40e).

## 4. Contrato del heartbeat (normativo)

```
POST /api/me/device-heartbeat
Headers:  Authorization: Bearer <device_token>
          X-Device-Token: <device_token>
Body:     {
            "applied_policy_version": int | null,   // policy_version del bundle vigente
            "uptime_seconds": int >= 0,             // uptime del agente Hermes
            "mode": "normal" | "restricted"         // modo operativo reportado
          }
200:      {
            "device_id": "<uuid>",
            "current_policy_version": int,
            "policy_update_available": bool,        // applied != current
            "heartbeat_interval_seconds": int,      // 300 default
            "online_window_seconds": int,           // 600 default
            "server_time": "<iso8601>",
            // SOLO camino plugin (Bearer = device token) — sliding renewal
            // (spec 022 v1.1.0 REQ-HRB-010): token fresco emitido en la
            // misma tx que ejecutó el DeviceGate; ausente en el camino sesión.
            "device_token": "<jwt>",                 // opcional
            "expires_in": int                        // opcional (segundos)
          }
401: device_not_authorized (revocado/desligado — fail-closed inmediato)
422: body inválido (uptime negativo, mode desconocido)
```

Efectos server-side por heartbeat: `last_heartbeat_at = now()`,
`applied_policy_version`, `agent_uptime_seconds` persistidos; audit según
D-027-05. El heartbeat NO emite `policy.propagated` cuando
`applied_policy_version` es null (primer arranque sin bundle) — solo fija la
línea base.

## 5. Requisitos (EARS) y criterios de aceptación

- **REQ-027-01 (M)** — El control plane DEBE exponer `POST
  /api/me/device-heartbeat` con la auth del bundle y persistir
  `last_heartbeat_at`/`applied_policy_version`/`agent_uptime_seconds` por
  dispositivo.
  → **AC-027-01**: heartbeat del camino plugin (Bearer device token) → 200 y
  la fila del dispositivo refleja los tres campos; revocado → 401
  `device_not_authorized`; body inválido → 422.
- **REQ-027-02 (M)** — Cuando el `applied_policy_version` reportado difiera
  del vigente, la respuesta DEBE indicar `policy_update_available: true` con
  el `current_policy_version`.
  → **AC-027-02**: admin actualiza policy (PUT /api/policies/*) → heartbeat
  con versión vieja → `policy_update_available: true` y
  `current_policy_version` = nueva.
- **REQ-027-03 (M)** — Un dispositivo DEBE aplicar la nueva policy sin
  reiniciar ni re-enrolar: re-baja del bundle JWS verificado y efectivo desde
  el siguiente turno, dentro de la ventana de consistencia.
  → **AC-027-03**: E2E con el `BundleSyncer` REAL del plugin contra el
  control plane REAL sobre HTTP: sync → v1; admin cambia policy; sync →
  bundle v2 aplicado (state.mode normal, policy_version 2) sin reinicio.
- **REQ-027-04 (M)** — El control plane DEBE exponer `GET
  /api/devices/presence` con el recuento y el detalle por dispositivo
  (quiénes: usuario, depto, dispositivo, última conexión, versión aplicada,
  estado), con dept-scope para dept_admin.
  → **AC-027-04**: presence refleja online/versión/uptime tras heartbeat;
  dept_admin solo ve sus deptos; user sin `devices.manage` → 403.
- **REQ-027-05 (M)** — El audit DEBE registrar `device.heartbeat` muestreado
  (≤1/h por dispositivo) y `policy.propagated` en cada transición confirmada.
  → **AC-027-05**: dos heartbeats seguidos → 1 fila `device.heartbeat`;
  heartbeat que confirma nueva versión → 1 fila `policy.propagated` con
  `{from_version, to_version}`; cadena verificable.
- **REQ-027-06 (M)** — La vista Devices del Admin Console DEBE mostrar en
  vivo cuántos y quiénes están conectados, con auto-refresco.
  → **AC-027-06**: build verde + la página consume `GET
  /api/devices/presence` (contador online, columnas Online/Policy/Last
  heartbeat) y refresca cada 15 s.
- **REQ-027-07 (M)** — Revocar un dispositivo DEBE quitarlo de los
  conectados: heartbeat → 401 y presence lo muestra offline (o no-online).
  → **AC-027-07**: revoke → heartbeat 401; presence: `online: false` y fuera
  de `online_count`.

## 6. Ventana de consistencia (documento operativo)

- Cambio de policy en el control plane → visible para el dispositivo en su
  próximo heartbeat (≤ `heartbeat_interval_seconds`, default 300 s).
- Re-baja + verificación del bundle: mismo ciclo (segundos).
- Aplicación del nuevo efectivo: siguiente turno del agente.
- **Peor caso total: intervalo de heartbeat + un turno.**
- Fail-closed: ver D-027-03. Offline: la policy anterior firmada sigue hasta
  agotar su `exp` (gracia offline del bundle); después, RESTRINGIDO.

## 7. Trazabilidad

- Migración: `20261026_0015_live_policy_presence` (down_revision
  `0014_auth_ux_policies`, anunciada en el card t_f4f99970 — regla 9).
- Seam plugin: comentario en t_c875626f con este contrato (§4).
- Matriz de capacidades (t_d8ed9362): sin nuevas policy keys — la presencia
  es infraestructura de gobernanza, no una capacidad del agente.
- Hotspots tocados: `db/migrations/versions/` (anunciado),
  `platform/app/devices/service.py` + `router.py` (ediciones aditivas),
  `platform/console/.../devices-page.tsx` (página de esta spec),
  `platform/app/governance/bundle.py` NO se edita (solo se importan
  `require_bundle_auth` y `_policy_version`; el endpoint vive en
  `platform/app/devices/heartbeat.py` nuevo para no colisionar con
  t_150e8931).
