# Plan técnico — Spec 006: Vault de credenciales, conectores y mensajerías

> Plan de implementación sobre el núcleo de spec 003 (`platform/`) y el schema canónico de spec 002 (sin migraciones nuevas: `vault_credentials` y `connector_instances` ya existen).

## 1. Módulos y archivos

| Archivo | Contenido |
|---|---|
| `platform/app/vault/service.py` | Ciclo de vida de credenciales: validación de gobernanza (tipos, conectores, scopes), cifrado (reusa `vault/crypto.py`), consultas con visibilidad por rol, rotación, revocación con corte, canal de uso |
| `platform/app/vault/router.py` | Router `/api/vault/credentials` (MfaGate en todas las rutas) |
| `platform/app/connectors/service.py` | Primitiva `disconnect_connector_instances()` + helpers de gobernanza de mensajería |
| `platform/app/connectors/router.py` | Extiende el router existente: `/available`, `/instances` CRUD |
| `platform/app/errors.py` | + `connector_not_allowed` (403), `credential_type_not_allowed` (422), `messaging_mode_not_allowed` (422) |
| `platform/app/main.py` | Registrar `vault_router` (edición mínima — hotspot compartido con specs 004/005/007/008) |
| `platform/tests/test_vault_connectors.py` | Suite nueva con tenant C propio (no toca el seed compartido de conftest) |

No se toca: `db/` (schema canónico paridad Alembic), `app/vault/crypto.py` (primitiva de spec 003), conftest.py (hotspot de specs hermanas).

## 2. Puntos de integración

- **Gobernanza (t07 / spec 005)**: `effective_policy_for_user(conn, principal)` de `app/governance/service.py` — sin cambios; esta spec solo consume `allowed_connectors`, `vault_allowed_credential_types`, `messaging_permissions`, `scoped_credentials`.
- **Corte por política (REQ-CON-005)**: spec 005 invoca `connectors.service.disconnect_connector_instances(conn, tenant_id, slug)` desde su vía de actualización de políticas; esta spec la usa en la revocación de credenciales.
- **Audit**: `audit.service.emit()` (hash chain por trigger del schema).
- **BYOK (spec 005, REQ-MDL-007)**: las keys BYOK son `vault_credentials` con `connector_slug = NULL`; el canal `use` es la vía de entrega por referencia.

## 3. Orden de implementación

1. `errors.py`: reasons nuevos.
2. `vault/service.py` + `vault/router.py` (registro → metadatos → rotación → revocación → uso).
3. `connectors/service.py` + `connectors/router.py` (instancias, available, desconexión, primitiva de corte).
4. `main.py`: registro del router.
5. Tests (tenant C aislado: tenant, dept, 3 usuarios, políticas org/user para denegaciones).
6. Converge: suite completa sobre PostgreSQL real, greps de seguridad, verificación de ACs.

## 4. Riesgos y mitigaciones

- **Hotspot `main.py` / `conftest.py`**: specs hermanas (004/005/007/008) corren en paralelo. Mitigación: diff mínimo en main.py; fixture de tenant C dentro del archivo de tests propio; comentario `hotspot:` en la tarjeta kanban.
- **Falso one-time**: el eco del valor en la respuesta de registro es el contrato §5.5 (D-VC-01); se testea que NO aparece en ninguna OTRA parte (AC-VC-002).
- **Doble puerta en mensajerías** (lista + map): decisión D-VC-03 documentada; deny-overrides exige ambas.
