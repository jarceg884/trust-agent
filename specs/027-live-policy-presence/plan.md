# Plan técnico — Spec 027

## Componentes

1. **Migración 0015** (`db/migrations/versions/20261026_0015_live_policy_presence.py`)
   - `devices` += `last_heartbeat_at timestamptz`, `applied_policy_version integer`,
     `agent_uptime_seconds integer`, `last_heartbeat_audit_at timestamptz`.
   - Seed `catalog.audit_actions`: `device.heartbeat`, `policy.propagated`.
   - Paridad `db/schema.sql` (§5.9 devices + §3 catálogo).
2. **Heartbeat (control plane)** — `platform/app/devices/heartbeat.py` (nuevo):
   - `POST /api/me/device-heartbeat` (APIRouter prefix `/api/me`), usa
     `require_bundle_auth` + `_policy_version` de `governance.bundle`
     (import, sin editar bundle.py — hotspot de t_150e8931).
   - `record_heartbeat(conn, principal, device_id, body)`:
     DeviceGate (`check_device_access(touch=False)`), UPDATE de los campos,
     comparación de versiones, audit muestreado + `policy.propagated`.
   - Settings nuevos en `config.py`: `heartbeat_interval_seconds` (300),
     `presence_online_window_seconds` (600, piso 60),
     `heartbeat_audit_interval_seconds` (3600).
   - Registro en `main.py`.
3. **Presencia** — `platform/app/devices/service.py` + `router.py` (aditivo):
   - `presence(conn, principal)` → recuento + detalle (JOIN users/departments,
     dept-scope `dept_scope` igual que `list_devices`).
   - `GET /api/devices/presence` (deps `_MANAGE`).
4. **Console** — `devices-page.tsx` + `types.ts`:
   - `useApi` sobre `/api/devices/presence` + poll 15 s (interval que llama
     `refetch()` de ambos hooks, cleanup al desmontar).
   - Franja "Live connections" (contador + badge Live) y columnas Online /
     Policy / Last heartbeat; badge "update pending" si applied < current.
5. **Tests** — `platform/tests/test_live_policy_presence.py`:
   - AC-027-01..02, 04, 05, 07 con `client` (TestClient) + fixtures canónicos.
   - AC-027-03 con `real_api` + `BundleSyncer` real del plugin (patrón
     AC-GOH-004 de test_governance_bundle.py): propagación v1→v2 sin reinicio.
6. **Docs** — `docs/live-policy-presence.md` (ventana de consistencia,
   fail-closed, runbook de verificación).

## Orden

migración → schema paridad → heartbeat endpoint → presence endpoint →
tests backend → console UI → build/lint → rebuild imágenes (migrate + api +
console) → verificación en vivo → docs.

## Riesgos

- **Hotspot migraciones**: head puede moverse mientras trabajo (lanes 0015+
  ajenas) — re-verificar `down_revision` antes de escribir y rebuild al final.
- **bundle.py intocable**: el endpoint importa helpers; si t_150e8931 mueve
  `_policy_version`, ajustar import (compat trivial).
- **Contenedores stale**: rebuild sin recreate es no-op (lección t_360a752e /
  t_b16825d0) — usar `--force-recreate`.
