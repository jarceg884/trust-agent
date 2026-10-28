# Attestation del agente + detección de overdue (spec 019, Incremento 3)

> Cada acción del agente queda auditada por la organización: al arrancar una
> sesión, el Hermes enrolado **atestigua** contra el control plane qué bundle
> firmado aplica, en qué modo opera y cuántos vetos acumula. Un dispositivo
> `active` que deja de atestiguar delata un agente desgobernado (p. ej. el
> plugin deshabilitado localmente) — el threat model honesto de ADR-018 no
> promete imposibilidad de bypass local; promete **detección**.

Spec: `specs/019-governance-over-hermes/spec.md` §3 D-GOH-06, §4
REQ-GOH-012/013/017, §9 Incremento 3 · Migración:
`0020_device_attestation` · Tests:
`platform/tests/test_governance_attestation.py` (10/10 PASS) · ADR-018 en
`docs/arquitectura.md`.

## Cómo funciona

### Attestation por sesión (REQ-GOH-012)

El plugin `trust-agent-governance` (módulo nuevo `attestation.py`, cableado
de forma aditiva en `register(ctx)`) envía en `on_session_start` — y como
one-shot de arranque, porque el flujo gateway/`-z` ensambla la sesión ANTES
de registrar los plugins de usuario (hallazgo 1 del Incremento 1) — un
POST contra el control plane:

```
POST /api/devices/{device_id}/attestation
Authorization: Bearer <device_token>   (camino plugin; también sesión+MFA)
X-Device-Token: <device_token>
{"bundle_hash": "<sha256 del bundle JWS vigente en caché>",
 "policy_version": 3,                  // null si no hay bundle vigente
 "mode": "normal",                     // "normal" | "restricted"
 "veto_counts": {"terminal": 3}}       // vetos desde la última attestation
```

El control plane (`platform/app/devices/attestation.py`, misma maquinaria
de auth que el governance-bundle: `require_bundle_auth` + fila `active` en
la transacción) lo registra:

- **Audit del tenant**: fila `device.attestation` con `tenant_id`,
  `device_id`, el hash del bundle, la policy_version, el modo y el conteo
  de vetos (cadena de hash inmutable, spec 008).
- **`devices.last_attestation_at`**: la señal de "agente reportando
  gobernanza" (nueva columna, migración 0020).

Reglas duras del contrato:

- El device del path debe ser EXACTAMENTE el del token (un token no
  atestigua dispositivos ajenos) → 401 `device_not_authorized`.
- Dispositivo revocado → 401 inmediato (revocación corta la attestation).
- Conteos negativos → 422 (nada de vetos falsificados).
- Sin bundle vigente la attestation SE ENVÍA igual (`mode=restricted`,
  `bundle_hash=null`): el agente restringido también es agente gobernado
  que reporta.

### Escalada de vetos (REQ-GOH-017)

`enforcement._count_veto` (Incremento 2) acumula los bloqueos por tool en
`$HERMES_HOME/trust-agent/veto-counts.json` "para la futura attestation".
Ese futuro es este: `attestation.py` fusiona ese archivo con
`state.veto_counts` (núcleo v2), viaja en `veto_counts`, y el control plane
destaca en el audit las tools con **3+ intentos bloqueados** como
`escalated_tools`. Tras un envío EXITOSO el conteo se consume (drenaje):
la próxima attestation parte de cero. Un error de red NO consume el conteo
— nada se pierde.

### Job de overdue (REQ-GOH-013)

`python -m app.devices.attestation_worker` (servio `attestation-worker`
del compose, patrón spend-worker, ciclo default 900 s):

1. Lista los tenants con dispositivos `active` (lectura cross-tenant por
   la conexión `trust_migrator` BYPASSRLS — patrón D-CP-10, nunca writes).
2. Por tenant, dentro de su scope (RLS): dispositivo `active` cuya
   referencia `COALESCE(last_attestation_at, approved_at, enrolled_at)`
   supere `attestation_overdue_hours` (policy key existente, default 24,
   piso 1) → **notificación `device_attestation_overdue`** a los org_admin
   (inbox real) + **audit `device.attestation_overdue`**.
3. **Sin duplicar dentro de la misma ventana**: la marca
   `devices.last_attestation_overdue_at` (columna nueva) dedupe — una
   notificación por dispositivo por ventana; una attestation llegada
   resetea el reloj. Crash-safe: el dedupe es una columna, nada en memoria.

## Verificación (evidencia real, nada simulado)

### Tests contra el control plane real

`platform/tests/test_governance_attestation.py` — 10/10 PASS (app FastAPI
real sobre HTTP contra la PostgreSQL efímera canónica):

- **AC-GOH-012**: attestation con bundle válido → fila `device.attestation`
  en `audit_log` del tenant con el hash sha256 correcto del JWS
  (verificado por SQL directo); modo restringido → attestation con el modo
  reportado; gates de auth (sin X-Device-Token / device ajeno / revocado →
  401; conteos negativos → 422).
- **AC-GOH-013**: dispositivo `active` aprobado hace 48 h sin attestation →
  el sweep genera la notificación al org_admin (fila en `notifications`) +
  la fila de audit; segunda pasada en la misma ventana → 0 duplicados;
  attestation llegada → el dispositivo deja de estar overdue; el worker
  real (`run_once`) ejecuta el ciclo cross-tenant idempotente.
- **AC-GOH-017**: 3 vetos de `terminal` + 1 de `browser` → la attestation
  siguiente porta `veto_counts={"terminal": 3, "browser": 2}` y el audit
  marca `escalated_tools=["terminal"]`; el conteo se consume tras el envío
  exitoso; `on_session_start` queda registrado vía `ctx.register_hook` y
  el callback no bloquea (envío en hilo daemon).

### Sesión real del perfil enrolado (DB viva del tenant)

Perfil Hermes real `trust-live-inc1` (dispositivo `fp-live-inc1`, tenant
`litellm-real4`, DB `trust_agent` viva), con device token renovado por el
flujo real (login PKCE S256 + TOTP `acr_values=2` → sesión MFA →
`POST /api/devices/{id}/token`; script reproducible:
`platform/scripts/evidence_attestation_live.py`):

```
$ hermes -p trust-live-inc1 -z "Reply with exactly: ATTESTATION-OK"
```

Log local del plugin (`$HERMES_HOME/trust-agent/plugin.log`):

```json
{"ts": 1793054059, "event": "attestation_sent", "trigger": "startup",
 "bundle_hash": "95b98524cf7c4197fe19dae351099547b6bbbc68e96c2e0d4fac08b53f835fd0",
 "policy_version": 1, "mode": "normal", "veto_counts": {}, "escalated_tools": []}
```

SQL contra la DB del tenant (verificable por cualquiera):

```sql
SELECT a.ts, a.action, a.payload->>'bundle_hash' AS bundle_hash,
       a.payload->>'policy_version' AS pv, a.payload->>'mode' AS mode
FROM audit_log a WHERE a.action = 'device.attestation' ORDER BY a.id;
```

```
             ts              |       action       | actor_role | bundle_hash                                                       | pv |  mode
-----------------------------+--------------------+------------+-------------------------------------------------------------------+----+--------
 2026-10-26 22:33:01.190716+00 | device.attestation | user       | 9859987ec2b5c8a69f62bd97cac218b5ed0d0abe31a4d65e6d304641864d7e19 | 1  | normal
 2026-10-26 22:34:19.281109+00 | device.attestation | user       | 95b98524cf7c4197fe19dae351099547b6bbbc68e96c2e0d4fac08b53f835fd0 | 1  | normal
(2 rows)

SELECT fingerprint, status, last_attestation_at FROM devices
WHERE fingerprint = 'fp-live-inc1';
-- fp-live-inc1 | active | 2026-10-26 22:34:19.281109+00
```

### Job de overdue en vivo

```
$ docker exec trust-agent-attestation-worker \
    python -m app.devices.attestation_worker --once
[attestation-worker] {'tenants': {'91eb5a4e-...': {'overdue_hours': 24, 'notified': 0, ...},
                                  ... 5 tenants con dispositivos active ...},
                      'notified': 0}
```

Ciclo cross-tenant completo contra la DB viva: 0 notificaciones (correcto —
los dispositivos activos del stack fueron aprobados hoy o atestiguaron; el
camino de notificación está cubierto por los tests AC-GOH-013 con la misma
maquinaria).

## Archivos

| Pieza | Archivo |
|---|---|
| Migración (columnas + acciones de audit) | `db/migrations/versions/20261026_0020_device_attestation.py` (+ paridad `db/schema.sql`) |
| Endpoint + sweep | `platform/app/devices/attestation.py` |
| Worker de overdue | `platform/app/devices/attestation_worker.py` (+ servicio `attestation-worker` en `docker-compose.yml`) |
| Plugin: módulo de attestation | `platform/hermes/plugins/trust-agent-governance/attestation.py` (NUEVO) |
| Plugin: drenaje del conteo | `.../state.py` (`drain_veto_counts`, aditivo) |
| Plugin: cableado | `.../__init__.py` (bloque aditivo coordinado con la lane t_c875626f) |
| Tests | `platform/tests/test_governance_attestation.py` |
| Script de evidencia live | `platform/scripts/evidence_attestation_live.py` |
