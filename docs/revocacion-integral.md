# Revocación integral de dispositivos — spec 019 Incremento 4 (REQ-GOH-011)

> Estado: **implementado y convergido con evidencia real** (t_07c5f196, 2026-10-26).
> Spec: `specs/019-governance-over-hermes/spec.md` §4 REQ-GOH-011, §9 Incremento 4, §8 threat model (D-GOH-05, ADR-017/018).

## 1. Qué garantiza

El threat model de la spec 019 (§8, D-GOH-07) promete **garantías duras remotas**:
"el admin revoca el dispositivo → conectores/vault/key mueren". Este incremento
cierra el GAP detectado por la revisión e2e del Pilar 3 (t_79df45e6): antes solo
moría el device token; ahora, al revocar un dispositivo **activo** con
`POST /api/devices/{id}/revoke`, en la **MISMA transacción** mueren además:

1. **La virtual key de LiteLLM del usuario** — bloqueo real en el gateway vía
   `POST /key/block` con el **hash** de la key (D-GOV-02). Un dispositivo
   revocado NO puede seguir invocando modelos aunque el usuario local retenga
   el plaintext de la key: el gateway responde **401 "Key is blocked"**.
2. **Las instancias de conectores activas del usuario** — sweep con la
   primitive de corte compartida (`disconnect_connector_instances`, REQ-CON-005,
   la misma del sweep por policy de spec 013). Las credenciales viven SOLO en
   el vault server-side (spec 006): sin instancia activa no hay canal de
   inyección runtime (`POST /api/vault/credentials/{id}/use` → **409**).

**Fail-closed**: si el gateway no puede bloquear la key (o el sweep falla), la
excepción propaga, la transacción hace rollback y la revocación **NO se
confirma** — el dispositivo sigue `active` (HTTP 503 `service_unavailable`).

**Dispositivo `pending`**: no dispara el sweep. Nunca canjeó device token ni
tuvo sesiones; no hay acceso real que cortar, y matar la key del usuario
cortaría el acceso que pertenece a sus OTROS dispositivos activos — más allá
de lo que la spec promete ("la revocación corta el acceso real" DEL
dispositivo revocado).

## 2. Decisiones contra el código real

- **Keys USER-scoped** (`platform/app/models/`): `api_keys.device_id` existe en
  el schema pero **ningún path de emisión lo puebla** — las virtual keys se
  emiten por usuario (D-GOV-15, REQ-GOV-032). D-GOH-05/REQ-GOH-011 dicen "la
  virtual key del usuario": se bloquean **todas las keys `active` del dueño**
  del dispositivo (por hash, vía el cliente real `LiteLLMClient.block_key`),
  y cada una queda `blocked` en `api_keys` + audit `model.key_blocked`
  (reason `device_revoked`). Un 404 del proxy (key ya inexistente) se trata
  como "ya muerta" y NO aborta (misma regla que `keys.revoke_key`).
- **Conectores**: no existe grant por dispositivo — las instancias son del
  usuario. Se barren las instancias `active` del dueño con la primitive
  compartida; cada corte audita `connector.disconnected` (reason
  `device_revoked`) y notifica al dueño por inbox (`connector_disconnected`).
- **Conexiones personalizadas (spec 024)**: no tienen fila de grant ni
  instancia; su canal de inyección es session-gated y **las sesiones del
  dispositivo se revocan en la misma transacción**, lo que corta también ese
  canal.
- **Catálogo de audit**: se usan las acciones EXISTENTES `model.key_blocked`
  (migración 0004) y `connector.disconnected` (migración 0001). El AC-GOH-011
  menciona `budget.key_blocked`, acción que no existe en el catálogo real; la
  acción canónica de bloqueo de key es `model.key_blocked`. Se usa la
  existente — **sin migración nueva** (hotspot `db/migrations/versions/`
  intacto por este incremento). Razón documentada aquí y en §12 de la spec.
- **Audit `device.revoked`**: el payload lleva el resumen del sweep
  (`sweep.keys_blocked` / `sweep.connectors_disconnected` con ids y aliases,
  sin secretos), de modo que la garantía dura es verificable desde la cadena
  de audit sola. La cadena sigue íntegra (`audit_verify_chain` → `ok`).

## 3. Coordinación con lanes hermanas

- La revocación → **restricted mode del plugin** es de la lane t_c875626f
  (spec 022 / Incremento 2, REQ-GOH-010). Este incremento NO toca el plugin:
  su alcance es lo que muere del **lado del control plane** (key + conectores).
- `platform/app/devices/service.py` es hotspot: las ediciones son **aditivas**
  (bloque nuevo al final; `revoke` extendido con el sweep, misma firma).

## 4. Tests (control plane real, DB efímera Alembic, doble LiteLLM declarado D-GOV-15)

`platform/tests/test_device_revocation_sweep.py` — **3/3 PASS**:

| Test | Verifica (AC-GOH-011) |
|---|---|
| `test_revoke_sweeps_virtual_key_and_connectors` | revocar device activo con key + conector → key `blocked` e invocación con la key retenida → **401**; instancia `disconnected` y uso del conector → **401** (sesión del device) / **409** (sesión fresca); audit `device.revoked` con resumen + `model.key_blocked` + `connector.disconnected` (reason `device_revoked`) + notificación inbox + cadena íntegra; **re-enrolamiento por fingerprint** (mismo device_id → approve → exchange → gate OK) |
| `test_revoke_fail_closed_when_gateway_down` | gateway inalcanzable → **503** + rollback total (device `active`, sesión activa, key `active`, sin audit); con el gateway de vuelta, la MISMA revocación completa el sweep |
| `test_revoke_pending_device_does_not_sweep` | revocar un `pending` NO mata la key del usuario (pertenece a sus otros dispositivos) |

Regresión: `test_devices_flow.py`, `test_devices.py`, `test_connector_policy_sweep.py`,
`test_vault_connectors.py`, `test_model_access.py`, `test_custom_connections.py` —
**61 passed**. Suite completa: **501 passed** con este incremento; los 2 únicos
fallos (`test_spend_cutoff.py::test_org_cutoff_and_tenant_isolation` y
`::test_notifications_endpoint_scoped`) son **preexistentes y ajenos a esta
lane** — fallan idéntico SIN el archivo de tests del incremento (atribución
2026-10-26: `--ignore=tests/test_device_revocation_sweep.py` → 2 failed,
498 passed) y pasan en aislamiento (8/8); es fuga de estado cross-test de
otros archivos sobre la DB efímera compartida. Reportados en tarea hija.

## 5. Evidencia LIVE contra el gateway LiteLLM real

Script reproducible: `platform/scripts/evidence_revocation_live.py`
(tenant `litellm-real3` — se eligió este tenant para no tocar los assets vivos
del Incremento 1/3 en `litellm-real4`).

```
platform/.venv/bin/python scripts/evidence_revocation_live.py --slug litellm-real3
```

Corrida del 2026-10-26 — **31/31 PASS**, resumen:

- **R0** control plane vivo (`/healthz` 200) + proxy real LiteLLM v1.106.3
  (`/health/liveliness` 200, upstream MaaS `glm-5.2`).
- **R1** login REAL PKCE S256 + TOTP (acr=2) del worker y del org_admin
  (Keycloak real, realm `litellm-real3`) → sesiones con MFA acreditada.
- **R2** virtual key real (`POST /api/models/keys` → `/key/generate` del
  proxy) → invocación `glm-5.2` → **200** con usage real de MaaS
  (pre-condición).
- **R3** gobernanza real: `PUT /api/policies/org` `allowed_connectors=["vmware"]`;
  credencial vault + instancia vmware `active`; inyección runtime → **200**
  (pre-condición).
- **R4** enrolamiento real (keypair EC local, fingerprint sha256) → 202 →
  aprobación admin → canje → device token.
- **R5** `POST /api/devices/{id}/revoke` → 200 con
  `sweep.keys_blocked` = las 3 keys activas del worker y
  `sweep.connectors_disconnected` = la instancia vmware.
- **R6** la key RETENIDA muere en el gateway real: invocación →
  **401 `{"message":"Authentication Error, Key is blocked.…","type":"auth_error"}`**;
  `api_keys.status = blocked`; `connector_instances.status = disconnected`;
  inyección con sesión fresca → **409 "No active connector instance"**.
- **R7** audit: `device.revoked` (1 fila) con el resumen del sweep en payload;
  `model.key_blocked` ×3 (reason `device_revoked`); `connector.disconnected`
  (reason `device_revoked`); notificación inbox al dueño;
  `audit_verify_chain` → **ok**.
- **R8** re-enrolamiento por fingerprint (spec 007 upsert): MISMO `device_id`
  → approve → exchange 200 → renovación del device token 200.

## 6. Cómo verificar

```bash
# 1) tests del incremento (DB efímera, doble LiteLLM declarado)
cd /root/trust-agent/platform
.venv/bin/python -m pytest tests/test_device_revocation_sweep.py -q   # 3 passed

# 2) suite completa
.venv/bin/python -m pytest tests/ -q

# 3) evidencia live (stack compose arriba, gateway real 127.0.0.1:4000)
.venv/bin/python scripts/evidence_revocation_live.py --slug litellm-real3

# 4) audit del tenant vivo (SQL)
docker exec trust-agent-db psql -U postgres -d trust_agent -c \
  "SELECT action, payload->'sweep' FROM audit_log \
    WHERE tenant_id = (SELECT id FROM tenants WHERE slug='litellm-real3') \
    AND action = 'device.revoked' ORDER BY seq DESC LIMIT 1;"
```

## 7. Trazabilidad

| Req | Decisión | Archivos |
|---|---|---|
| REQ-GOH-011 (a) bloqueo de virtual key | D-GOH-05, D-GOV-02, D-GOV-15 | `platform/app/devices/service.py::_block_user_virtual_keys`, `platform/app/models/litellm.py::block_key` |
| REQ-GOH-011 (b) sweep de conectores | D-GOH-05, REQ-CON-005, spec 013 D-013-02 | `platform/app/devices/service.py::_sweep_device_connector_grants`, `platform/app/connectors/service.py::disconnect_connector_instances` |
| Fail-closed (misma transacción) | spec 019 §9 Inc. 4 | `platform/app/devices/service.py::revoke` + `platform/app/errors.py::service_unavailable` |
| Audit con resumen + cadena íntegra | constitution IV | `audit_service.emit` en `revoke` (payload `sweep`) |
| Re-enrolamiento posterior | spec 007 upsert | `enroll` (sin cambios — verificado por test/live) |
