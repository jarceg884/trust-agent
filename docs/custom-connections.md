# Conexiones personalizadas en el vault (spec 024)

> Mandato de Miller: en conexiones/vault hay que considerar conexiones
> **personalizadas** — bases de datos, API keys, etc. Esas conexiones se guardan
> en el vault y se ponen en la policy: **el admin de la org decide qué
> conexiones agregar/permitir**.

## Qué es una conexión personalizada

Un recurso de **organización** (no una credencial de usuario) definido por el
`org_admin` más allá del catálogo fijo de conectores (`vmware`, `email`,
`slack`, `teams`, …). Tres kinds:

| Kind | Metadata NO-secreta requerida | Secreto (cifrado) |
|------|-------------------------------|-------------------|
| `database` | `type`, `host`, `port` (1-65535), `database`, `username` | password de la BD |
| `api_key` | `service`, `auth` (`bearer`\|`header`), `header_name` (si header) | la API key |
| `custom` | JSON libre | el secreto que sea |

El secreto se cifra con el **mismo envelope crypto del vault** (KEK/DEK
AES-256-GCM, `platform/app/vault/crypto.py`) y se muestra **EXACTAMENTE una
vez** en la respuesta de create/update (patrón one-time de las API keys).
Las lecturas (list/get) devuelven metadata únicamente — jamás secreto ni
ciphertext. Toda clave de metadata que parezca un secreto
(`password`/`secret`/`token`/`api_key`/`credential`) se rechaza con 422: el
secreto vive cifrado en su propia columna.

## API (RBAC: `connections.manage`, solo org_admin)

| Método | Ruta | Notas |
|--------|------|-------|
| `POST` | `/api/vault/connections` | 201; body `{name, kind, metadata, secret_value}`; devuelve `secret_value` UNA vez. Audit `connection.created`. |
| `GET` | `/api/vault/connections` | Metadata solo. org_admin ve todo el tenant; el resto solo lo que su policy efectiva permite. |
| `GET` | `/api/vault/connections/{id}` | Metadata; 404 sin oracle de existencia para ids no permitidos. |
| `PUT` | `/api/vault/connections/{id}` | Update metadata/name; `new_secret_value` opcional rota el secreto (one-time). Audit `connection.updated`. |
| `POST` | `/api/vault/connections/{id}/revoke` | Irreversible e idempotente. Audit `connection.revoked`. |
| `POST` | `/api/vault/connections/{id}/use` | **Inyección runtime** (REQ-024-007): gate de policy, audit `connection.used` SIN secreto. |

## Policy `allowed_connections` (deny-overrides, 3 niveles)

Key tipada del catálogo (`list`, default `["*"]`) en org → depto → usuario con
la maquinaria existente (intersección deny-overrides, no-relajación). Sus
tokens:

- `"*"` — todos los conectores del **catálogo** (siguen sujetos a
  `allowed_connectors`). Las personalizadas SIEMPRE requieren id explícito.
- slug del catálogo — un conector concreto.
- **id (UUID)** de una conexión personalizada ACTIVA del tenant.

Regla combinada para un slug del catálogo: utilizable iff
`slug ∈ allowed_connectors` Y (`"*" ∈ allowed_connections` O
`slug ∈ allowed_connections`). Un token desconocido/inexistente en el upsert
responde **422** (typo-protection, D-24-07). El default `["*"]` mantiene a los
tenants existentes sin cambio de comportamiento hasta que su admin fije la key.

### Corte al cambiar la policy

- **Conectores del catálogo** (con estado persistente): el sweep existente
  (`sweep_after_policy_change`, `governance/policies.py`) corre en la MISMA
  transacción del upsert de `allowed_connections` y corta las instancias
  activas que la nueva policy efectiva ya no permite (audit
  `connector.disconnected`, reason `policy_updated`, notificación).
- **Conexiones personalizadas** (sin estado persistente, D-24-05): la
  inyección es por-uso — el gate de policy aplica en el uso SIGUIENTE (403
  `connector_not_allowed`; 410 si está revocada). Corte inmediato de facto.

## Governance bundle (REQ-024-010)

El bundle JWS lleva el claim ADITIVO `allowed_connections`:
`{"catalog": [slugs efectivos], "custom": [{id, kind, name, metadata}]}`
— metadata NO-secreta, nunca secretos. El claim es OPCIONAL (no está en los
REQUIRED_CLAIMS de la spec 019 §5.2): los bundles firmados antes del cambio
siguen verificando y el verificador PyJWT del plugin ignora claims
desconocidos (schema versionado/extensible, coordinado con t_f99e12ab /
t_c875626f).

## UI Admin Console

- **Credentials → Custom connections**: CRUD completo con formulario por
  kind, secreto one-time (diálogo "Connection saved"), rotación y revocación.
- **Organization → New policy** con key `allowed_connections`: selector de
  tokens (wildcard, catálogo, personalizadas).
- **Departments → Policies**: editor de `allowed_connections` por depto.
- **Users → Policies** (por fila): editor de `allowed_connections` por usuario.

## Datos y aislamiento

- Tabla `vault_connections` (migración `0012_custom_connections`, paridad
  `db/schema.sql` §14): `tenant_id` + RLS ENABLE+FORCE con la política
  `tenant_isolation` (fail-closed), índice tenant-first, UNIQUE
  `(tenant_id, name)`, trigger `set_updated_at`.
- Permisos: `connections.manage` otorgado únicamente a `org_admin`
  (RBAC data-driven, spec 017).
- Audit: `connection.created/updated/revoked/used` (enum versionado, payload
  sin secreto).

## Verificación

```bash
# Suite de la spec (AC-24-001..005)
cd platform && .venv/bin/python -m pytest tests/test_custom_connections.py -q

# Suites afectadas
.venv/bin/python -m pytest tests/test_connector_policy_sweep.py \
  tests/test_governance_bundle.py tests/test_vault_connectors.py -q

# Aislamiento RLS (db/tests)
cd ../db && TRUST_DB_CONTAINER=trust-agent-db .venv/bin/python -m pytest tests/ -q
```

Spec: `specs/024-custom-connections/` (spec.md, plan.md, tasks.md).
