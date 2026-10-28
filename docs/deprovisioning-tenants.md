# Deprovisioning de tenants y convención anti-basura

> Tarea t_4f4b0eb1 (repro Miller: *"ese montón de clientes — ¿hay cómo
> eliminarlos? Yo no he creado ninguno y no veo botón para eliminar."*).
> Estado: implementado y verificado (2026-10-28).

## 1. Deprovisioning real: `DELETE /api/admin/tenants/{slug}`

La inversa de `provision()`. Borra un tenant COMPLETO:

- **Keycloak**: `DELETE /admin/realms/{realm}` (Admin REST, el mismo
  cliente de provisioning).
- **PostgreSQL**: UNA transacción como `trust_migrator` (BYPASSRLS) que
  borra todas las filas del tenant por `tenant_id` en orden inverso a
  las FK, incluyendo `audit_log` (excepción de purga `app.audit_purge`,
  la misma del job de retención) y las filas BYOK del tenant en el
  catálogo global `catalog.model_catalog`.
- **Audit**: evento durable `tenant.deleted` en `ops.provider_audit`
  (plano proveedor, cadena global append-only). NO puede vivir en
  `audit_log`: esa tabla exige `tenant_id` + FK a `tenants` y se borra
  CON el tenant.

Orden de borrado (dependientes primero) y el pitfall BYOK están
documentados en `platform/app/tenants/deprovision.py`.

### Superficie

| Capa | Dónde |
|------|-------|
| API | `DELETE /api/admin/tenants/{slug}` con body `{"confirm": "<slug>"}` — `platform/app/tenants/router.py` |
| CLI | `trust-provision deprovision <slug> --confirm <slug>` — `platform/app/tenants/cli.py` |
| UI | Botón **"Delete tenant…"** en el detalle del tenant del Provider Console; el diálogo exige ESCRIBIR el slug exacto para habilitar el botón — `tenant-detail.tsx` + `ConfirmDialog(requireText=…)` |
| Sweeper | `platform/scripts/sweep_test_tenants.py` (bulk, dry-run por defecto) |

### Reglas de seguridad

- **Solo el provider** (super-user), con el permiso `tenants.delete`
  (nuevo, MÁS restrictivo que `tenants.provision`; check data-driven
  contra `catalog.role_permissions` — quitar la fila niega el endpoint).
- **Fail-closed**: `confirm` debe ser el slug EXACTO. Cualquier otra
  cosa → 422 sin tocar nada. La UI replica el mismo check (el botón
  queda deshabilitado hasta escribir el slug exacto).
- **Irreversible**: no hay undo. El diálogo lo dice explícito.
- Si el borrado del realm falla, las filas YA se borraron (transacción
  commitada) y la respuesta lleva `realm_deleted: false` + `warning`
  explícito — un realm huérfano es inerte (sin fila en `tenants` no se
  minta ninguna sesión) y el re-provisioning del mismo slug lo cura.

## 2. Convención anti-basura de la fábrica

**Problema**: 104 tenants en la BD real, casi todos basura de pruebas de
la fábrica (test2, panel-e2e-*, e2e-act-*, gate-final, b3-*, qa-*,
dev-f1-*, pilar2-*, dyn-disc-*, catalog-sync-*, litellm-real*, …). El
console de Miller se llenó de probes porque los tests/e2e/evidence de la
fábrica corrían contra el stack real y nunca limpiaban.

**Reglas** (obligatorias para TODO lo que la fábrica corre contra el
stack real):

1. **Prefijo canónico**: todo tenant creado por e2e/tests/evidence/QA
   de la fábrica lleva slug con prefijo `e2e-` (o `test-`). Nunca un
   nombre que parezca un cliente real.
2. **Limpieza al terminar**: el propio harness deprovisiona su tenant
   al final (el e2e de browser llama al DELETE real en su `finally`;
   ver `platform/provider-console/e2e/*.mjs`). Los módulos pytest que
   tocan Keycloak real limpian TODOS los realms que crean en su fixture
   (ver `_realm_cleanup` de `test_provision.py`).
3. **Red de seguridad**: `platform/scripts/sweep_test_tenants.py`
   barre todo slug que matchee los patrones de fábrica. Dry-run por
   defecto; `--yes` ejecuta; `--keep` protege slugs; `--from-file`
   para listas curadas. Se puede correr tras una sesión de pruebas:

   ```sh
   # dentro del contenedor del API (mismo entorno que el control plane)
   docker cp platform/scripts/sweep_test_tenants.py trust-agent-api:/tmp/
   docker exec trust-agent-api python /tmp/sweep_test_tenants.py --dry-run
   ```

## 3. Limpieza histórica (2026-10-28)

Borrados **95 tenants** de prueba de la BD real (filas + realms de
Keycloak + evento `tenant.deleted` cada uno), dejando solo los
candidatos a "reales" pendientes de confirmación de Miller:
`arce-demo`, `demo-org`, `globex-industries`, `spec020-demo`,
`hermes-bridge`, `hermes-dl`, `miller-selfsignup-r1/r2/r3`.

Evidencia de la limpieza: 0 filas huérfanas en todas las tablas con
`tenant_id`, 0 filas BYOK huérfanas en `catalog.model_catalog`, 95
eventos `tenant.deleted` en `ops.provider_audit`, 2 realms huérfanos
históricos (`delete-corp`, `explicit-plan-corp`) también eliminados.

### Segunda pasada (2026-10-28, decisión de Miller)

Miller confirmó que NINGUNO de los 9 candidatos era suyo — empieza
limpio y creará su tenant desde cero. Borrados los 9 (`arce-demo`,
`demo-org`, `globex-industries`, `spec020-demo`, `hermes-bridge`,
`hermes-bridge`, `hermes-dl`, `miller-selfsignup-r1/r2/r3`) más 3 fixtures de una lane
ya terminada (`millrepro`, `miller-dept-repro`, `miller-dept-signup`):
**12 tenants** vía `sweep_test_tenants.py --from-file --yes`, con
realm de Keycloak y evento `tenant.deleted` cada uno. Una tercera
pasada barrió 3 fixtures de lanes UI terminadas (`policies-ux`,
`spec037-ui`, `console-smoke2`). Total histórico: **110 tenants de
prueba eliminados** (más 2 fixtures `e2e-*` auto-limpiados por sus
propios tests — 112 eventos `tenant.deleted` en `ops.provider_audit`);
0 filas huérfanas en cada pasada.

**Verificación del botón** (click-through en browser real, nada
simulado): `platform/provider-console/e2e/delete-panel.mjs` — 13/13
PASS (fixture vía self-signup real, login real del operador, botón
visible, ConfirmDialog fail-closed con slug erróneo, DELETE con body
plano `{confirm: slug}`, HTTP 200 `deleted:true` + `realm_deleted:true`,
redirect a /tenants y tenant ausente de la lista).

Los fixtures de lanes ACTIVAS no se tocan hasta que su lane termine
(sus patrones ya están en el sweeper para la higiene continua):
`miller-prov-*` (model-providers) y `estructura-viva` (spec 038, en
review). Cuando esas lanes cierren, una sola pasada de
`sweep_test_tenants.py --patterns --yes` deja la consola en 0.

### Bug encontrado durante el barrido (arreglado)

`trust_migrator` no tenía `DELETE` sobre 6 tablas creadas después del
grant blanket de la migración 0001 (`agents`, `agent_grants`,
`notifications`, `vault_connections`, `branding_assets`,
`branding_versions`) — el deprovisioning reventaba con
`InsufficientPrivilege`. Curado con un re-grant blanket idempotente en
la migración `0031_tenant_deprovision` (y su paridad en
`db/schema.sql` §23).

Además: las filas BYOK del tenant en `catalog.model_catalog` deben
borrarse ANTES que `vault_connections` — el FK `connection_id` es
`ON DELETE SET NULL` y el SET NULL viola `model_catalog_source_scope_check`
(byok exige conexión). Regresión cubierta en
`platform/tests/test_deprovision.py::test_deprovision_tenant_with_byok_catalog_rows`.
