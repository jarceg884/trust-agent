# Plan técnico — Spec 016

## Componentes tocados

| Capa | Archivo | Cambio |
|------|---------|--------|
| DB catálogo | `db/migrations/versions/20261026_0009_departments_crud.py` | INSERT `department.renamed` / `department.deleted` en `catalog.audit_actions` (sección §11 embebida, patrón 0005) |
| DB catálogo | `db/schema.sql` §11 | Misma sección verbatim (paridad AC-DB-040) |
| API service | `platform/app/org/departments.py` | `create_department` acepta `initial_budget_cents` (llama `budgets.set_dept_budget` en la misma tx); nuevos `rename_department` y `delete_department` |
| API router | `platform/app/org/router.py` | `DepartmentBody.initial_budget_cents`; `PUT /departments/{id}`; `DELETE /departments/{id}` (ambos con gate `departments.manage`) |
| UI | `platform/console/src/app/(console)/departments/departments-page.tsx` | Formulario create real, rename, delete con `ConfirmDialog`, toasts/loading, comentario actualizado |
| Tests | `platform/tests/test_departments_crud.py` | REQ-16-100…104 |

## Secuencia

1. Migración 0009 + schema.sql §11 → paridad `db/tests` en verde.
2. `departments.py` service (reglas D-16-01/02/03) → router.
3. Tests nuevos → suite completa.
4. Rebuild imágenes (`api`, `console`) + `migrate` en el stack vivo.
5. Evidencia UI por navegador (tenant de evidencia aprovisionado con
   provider token) + audit por SQL.

## Riesgos

- **FK de `spend_ledger`/`agents`/`user_roles`**: el DELETE explícito
  chequea cada bloqueo ANTES del `DELETE FROM departments`; cualquier fila
  residencial sería IntegrityError → por eso la cascada de config se hace
  primero y los bloqueos se validan con conteos.
- **Paridad pg_dump**: solo INSERT de catálogo (patrón 0005/0008), sin DDL.
- **Console rebuild**: la imagen del console se construye desde fuente;
  sin rebuild no hay evidencia UI de los cambios.
