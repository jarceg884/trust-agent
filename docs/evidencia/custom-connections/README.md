# Evidencia real — Spec 024: conexiones personalizadas en el vault

> Corrida de verificación final: 2026-10-26 12:20–12:35 CST, contra el stack
> vivo del compose (api :8000, console :3100, PostgreSQL compartida con RLS,
> migraciones hasta 0014 aplicadas). Sesión admin REAL minteada contra el
> control plane (patrón `console/scripts/smoke_authorized.py`: IdP de test
> JWKS + `POST /api/auth/session`, sesión en PostgreSQL) e inyectada en el
> navegador para operar la UI con clics reales. Nada simulado.

## 1. Screenshots (browser automation, clics reales)

| # | Archivo | Qué muestra |
|---|---------|-------------|
| 1 | `01-onetime-secret-dialog.png` | Credentials → Custom connections → Add connection (kind=database: type/host/port/database/username + secret) → **diálogo one-time "Connection saved"** con el secreto visible EXACTAMENTE una vez ("Analytics DB prod", secreto `PgSuper…OneTime`). |
| 2 | `02-list-metadata-only.png` | Lista de conexiones: "Analytics DB prod" (active) y "Ana" (revoked) con metadata JSON; **sin secreto ni ciphertext en ninguna lectura**. |
| 3 | `03-org-policy-allowed-connections.png` | Organization → New policy → key `allowed_connections`, value type "Connections (spec 024)": selector con wildcard `*`, los 6 slugs del catálogo y la conexión custom ACTIVA (la revocada no aparece). Guardada: `["*","e1a5dd54-…"]` (v1). |
| 4 | `04-dept-policy-tightened.png` | Departments → Cloud Operations → Policies → Allowed connections **estrechado a solo la custom** `["e1a5dd54-…"]` (sin `*`); el guardado re-corrió el sweep ("cut off any instance the new effective policy no longer allows"). |
| 5 | `05-audit-connection-events.png` | Audit Log con `connection.created` (×2), `connection.revoked` y `policy.updated` (org y depto) — actores y targets reales. |
| 6 | `06-user-level-editor.png` | Users & Roles → Policies (por usuario): editor `allowed_connections` nivel usuario con default heredado `["*"]` (deny-overrides: solo puede estrechar). |

## 2. Gate runtime verificado en vivo (API real, sesión admin, CSRF)

```
POST /api/vault/connections/{e1a5dd54…}/use   → 200  secret descifrado in-memory (permitida por policy org)
POST /api/vault/connections/{2aecd7f7…}/use   → 410  gone "Connection is revoked."
POST /api/vault/connections/{e3db0f51…}/use   → 403  connector_not_allowed "not allowed by policy (allowed_connections)"
PUT  /api/policies/org  token UUID inexistente → 422  validation_failed "token … is not an active custom connection"
```

La conexión `e3db0f51` (api_key "Temp API key") se creó fuera de la policy
para el 403 y se revocó de inmediato (cleanup auditado).

## 3. Tests (re-ejecutados de primera mano en esta corrida)

```
platform: tests/test_custom_connections.py + test_connector_policy_sweep.py
        + test_governance_bundle.py + test_vault_connectors.py + test_vault_crypto.py
        → 53 passed
db/tests (TRUST_DB_CONTAINER=trust-agent-db) → 180 passed, 2 failed
```

Los 2 fallos de db/tests son PRE-EXISTENTES y atribuidos a la migración
**0014_auth_ux_policies** de la lane auth UX (spec 015: `require_mfa` pasó a
`invariable=false` y los tests `test_catalog_seeds_present` /
`test_invariable_policy_keys_are_catalog_data` quedaron stale) — ya reportados
a esa lane (comentario #70 de t_1844fb06 vía t_e0943bd8). La migración 0012 de
esta spec solo INSERTA `allowed_connections` (verificado línea 61).

Fallos de suite completos NO relacionados con esta spec (atribuidos a la lane
de provisioning/provider, `provision.py` mtime 11:45 > mis archivos 10:36,
error real `keycloak.mapper: ProtocolMapper provider not found`):
`test_provision.py` (8), `test_provider_admin.py` (2), `test_spend_cutoff.py` (1).

## 4. Hallazgo de esta corrida (ya corregido)

El contenedor `trust-agent-console` corría una imagen stale (creada 11:26 CST,
anterior al rebuild 11:50 que incluía la UI de spec 024) — la sección "Custom
connections" no aparecía. Corregido con
`docker compose up -d --no-deps --force-recreate console api` (mismo patrón
que reportó t_360a752e para provider-console: rebuild de imagen sin recrear
el contenedor es un no-op silencioso).

## 5. Revalidación completa (opcional)

`evidence_custom_connections.py` (escrito en un run anterior) ejecuta el
journey completo con tenant provisionado + login PKCE/TOTP real + bundle JWS;
hoy queda bloqueado por el bug de realms nuevos de la lane auth UX
(t_1844fb06, ver `heal_realm_flow.py`). Cuando esa lane converja, re-ejecutar
para la evidencia con slug fresco. La evidencia de esta corrida usa el tenant
`demo-org` pre-existente y es 100% real.

Spec: `specs/024-custom-connections/` · Doc: `docs/custom-connections.md`
