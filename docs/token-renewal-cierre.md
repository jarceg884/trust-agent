# Cierre — Renovación del device token (gap t_0b6e8e2d, spec 022 v1.1.0)

**Fecha**: 2026-10-27 · **Severidad**: ALTO · **Estado**: CONVERGIDO con evidencia live

## El gap

En la app real embarcada (Hermes + plugin trust-agent-governance + trust-login) el
device token (TTL 15 min, `device_token_ttl_minutes`) no tenía NINGÚN camino de
renovación: heartbeat/bundle no re-emitían token, nada llamaba
`POST /api/devices/{id}/token`, la recuperación documentada de D-HRB-03 moría en
409/410 para un device `active`, y el watcher de auto-enroll cortaba por presencia
del archivo sin chequear expiración. Resultado: sesiones > 15 min rotas (401 en
bucle → restricted), expiración indistinguishable de revocación, y recuperación
solo con admin revocando+re-aprobando cada ventana de 15 min. Fail-closed se
mantenía (REGLA DE ORO intacta — el agente quedaba MÁS restringido), pero el
producto era inusable. Evidencia del bug: docs/revision-pilar3-cierre.md §Punto 2/3,
docs/evidencia/pilar3-qa/.

## El fix (spec-first: spec 022 amend v1.1.0, REQ-HRB-010 a/b/c; spec 027 §4 amend)

1. **Sliding renewal por heartbeat (REQ-HRB-010a)** — `POST /api/me/device-heartbeat`
   con Bearer=device token (camino plugin) responde además `device_token` +
   `expires_in` frescos, emitidos en la MISMA transacción que ya corrió el
   DeviceGate. La revocación sigue fail-closed: un device revocado da 401 ANTES de
   llegar a la emisión. El plugin persiste el token renovado en el mismo ciclo
   (`renewed_at` en device-token.json, evento `device_token_renewed`).
   Mientras el agente esté vivo y el device `active`, la sesión no caduca nunca
   por TTL.
2. **Re-emisión directa con re-autenticación (REQ-HRB-010b)** — `POST /api/devices/enroll`
   con id_token OIDC fresco (acr≥2, verificado por JWKS) para un device `active`
   del MISMO usuario responde **200** con token directo (audit
   `device.token_reissued`), sin re-aprobación admin: el device ya estaba
   aprobado y el usuario re-probó identidad con MFA. El 409 se conserva SOLO
   para fingerprint propiedad de otro usuario; `revoked`/`rejected` siguen el
   flujo histórico pending+aprobación (fail-closed).
3. **Auto-recuperación del plugin (REQ-HRB-010c)** — el BundleSyncer detecta la
   expiración local (claim `exp`), reporta `device_token_expired` (razón honesta,
   distinta de `revoked`), intenta UN re-enroll con el `id-token.txt` fresco de
   trust-login y recupera en el mismo arranque (`device_token_recovered`); sin
   id_token fresco entra en `restricted(token_expired)`. El watcher de auto-enroll
   ya no corta por presencia del archivo sin chequear expiración.

**Recuperación documentada (D-HRB-03 amend)**: "re-ejecutar `trust-login`
(SSO+MFA) → el plugin re-emite el token en el siguiente ciclo". Sin 409/410 muerto.

### Archivos

- `platform/app/devices/heartbeat.py` — sliding renewal (camino plugin).
- `platform/app/devices/service.py` — re-emisión directa para device active (200),
  audit `device.token_reissued`; 409 solo para fingerprint ajeno.
- `platform/hermes/plugins/trust-agent-governance/bundle_client.py` —
  `device_token_expired()`, `save_renewed_device_token()`, eventos
  `device_token_expired`/`device_token_renewed`/`device_token_recovered`.
- `platform/hermes/plugins/trust-agent-governance/enroll.py` — chequeo de
  expiración en el watcher, `recover_expired_token()`.
- `specs/022-hermes-real-bridge/spec.md` — v1.1.0: D-HRB-03 reescrita + REQ-HRB-010.
- `specs/027-live-policy-presence/spec.md` — §4 amend (heartbeat devuelve token).
- `docs/hermes-real-bridge.md` — restricciones actualizadas al camino real.
- `platform/scripts/evidence_token_renewal_live.py` — evidencia reproducible (5 fases).
- Tests: `platform/tests/test_hrb_heartbeat.py` (AC-HRB-010a),
  `platform/tests/test_devices_flow.py`, `platform/tests/test_live_policy_presence.py`.

## Evidencia LIVE (script `evidence_token_renewal_live.py`, tenant hermes-dl-e2e)

Stack real: api :8000 (imagen reconstruida con el fix), Keycloak :8080, gateway
LiteLLM, PostgreSQL. Home `.e2e/hermes-dl-home-renewal`, device `df3628ef`.
Artefactos: `docs/evidencia/token-renewal/` (00–08).

| Fase | Resultado | Qué prueba |
|---|---|---|
| setup | 10/10 PASS | bundle fresco → install → login PKCE+TOTP acr=2 → auto-enroll zero-UI → approve |
| renewal | 8/8 PASS | **AC1**: NORMAL sostenido MÁS ALLÁ del TTL original; token rotó (`renewed_at`); sin 401 |
| expiryrecovery | 7/7 PASS | **AC3**: agente OFF > TTL → token muerto de verdad → trust-login → recuperación AUTOMÁTICA (200 directo, audit `device.token_reissued`, NORMAL sin admin) |
| revocation | 6/6 PASS | **AC2**: admin revoca a mitad de sesión → 401 → RESTRICTED inmediato; BD revoked; cero heartbeat_ok tras el 401 (AC-HRB-004c intacto) |
| audit | 6/6 PASS | la corrida entera en audit_log del tenant; `device.token_reissued` con fingerprint |

**AC1 en bruto**: la sesión enrolada vivió **179 min** (08:45→11:25) con
**328 heartbeat_ok + 328 device_token_renewed** y **0 device_unauthorized**
(`04-plugin-log-expiryrecovery.txt` contiene el log completo). El TTL original
era 15 min.

## Tests

- Plataforma: **631/632** — el único fallo (`test_capability_enforcement.py::
  test_no_core_patches`) es de una lane hermana: `capability_enforcement.py`
  (mtime 08:50, lane de capability keys) menciona "hermes_cli" en comentarios y
  trips el guardián literal. No relacionado con este fix.
- Renovación (heartbeat/devices/live-policy): **43/43**.
- DB: **207/207** (`TRUST_DB_CONTAINER=trust-agent-db`).

## Notas para QA (re-verificación prescrita por el reporte del Pilar 3)

1. Sesión enrolada mantiene NORMAL > 15 min → **verificado** (179 min, arriba).
2. Revocación sigue fail-closed inmediato → **verificado** (fase revocation).
3. La recuperación documentada funciona sin 409/410 → **verificado**
   (fase expiryrecovery).
4. Reproducir: `platform/scripts/evidence_token_renewal_live.py --phase setup`
   → `renewal` → `expiryrecovery` → `revocation` → `audit` (stack con imagen
   reconstruida; `TRUST_SYNC_INTERVAL=30` para ciclos rápidos).
5. La revocación integral de la fase revocation bloqueó también la virtual key
   del worker (sweep REQ-GOH-011) — restaurarla es flujo de budget, no de esta
   corrida.
