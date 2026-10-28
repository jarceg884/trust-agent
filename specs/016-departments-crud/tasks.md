# Tasks — Spec 016

- [x] T1 — Migración `0009_departments_crud` + `db/schema.sql` §11 (audit
  actions `department.renamed`/`department.deleted`); paridad en verde.
- [x] T2 — Service: `initial_budget_cents` en `create_department`,
  `rename_department`, `delete_department` (bloqueos 409 + cascada config).
- [x] T3 — Router: `PUT /api/departments/{id}`, `DELETE /api/departments/{id}`,
  body extendido; gate `departments.manage`.
- [x] T4 — Tests `platform/tests/test_departments_crud.py`
  (REQ-16-100…104) + suite completa sin regresiones.
- [x] T5 — UI `departments-page.tsx`: create/rename/delete + confirmación +
  toasts + loading; comentario desactualizado corregido.
- [x] T6 — Stack vivo: migrate + rebuild api/console; evidencia real desde
  la UI (create → rename → delete) con audit verificado por SQL.
