# Spec 016 — Departments CRUD completo: rename, delete y UI conectada (feedback Miller #3)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | dev (tarea t_955c98d6, feedback Miller #3) |
| **Padre** | specs/012-org-endpoints/spec.md §1 (follow-up declarado: "DELETE/Rename de departamentos (CRUD completo)"), specs/009-admin-console/spec.md §4.2 D-AC-06, specs/005-governance/spec.md (budgets jerárquicos), specs/008-audit/spec.md §5 (contrato de eventos) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema (feedback #3)

La página **Departments** del Admin Console no deja crear ni eliminar: el
diálogo "Add department" muestra un aviso DESACTUALIZADO ("the control plane
does not expose a department creation endpoint yet") cuando `POST
/api/departments` YA existe (spec 012, 201 verificado en vivo). `DELETE` no
existe en el control plane y el rename tampoco.

## 2. Alcance

**Dentro de alcance**:

1. **Extensión de `POST /api/departments`** (spec 012, evolución
   backward-compatible): campo opcional `initial_budget_cents` — alta de
   departamento + presupuesto mensual en UNA transacción atómica (reutiliza
   la validación de `budgets.set_dept_budget`: Σ dept ≤ org, org budget
   debe existir → 422 y rollback total si falla).
2. **`PUT /api/departments/{id}`** — rename con audit `department.renamed`.
3. **`DELETE /api/departments/{id}`** — baja con reglas de negocio (§D-16-02)
   y audit `department.deleted`.
4. **UI completa** en `departments-page.tsx`: crear (nombre + budget inicial
   opcional), renombrar, eliminar con confirmación, toasts, estados de
   carga; actualización del comentario desactualizado.
5. **Migración `0009_departments_crud`** + `db/schema.sql` §11: dos acciones
   nuevas de audit (`department.renamed`, `department.deleted`) — solo
   INSERT de catálogo, cero DDL (paridad pg_dump intacta).
6. **Tests** nuevos en `platform/tests/test_departments_crud.py`.

**Fuera de alcance**:

- Reasignación masiva de usuarios en el DELETE (v1 bloquea con 409 y el
  mensaje indica el camino existente: mover usuarios con
  `PUT /api/users/{id}` antes de eliminar).
- Baja de departamentos con gasto histórico (inmutable por gobernanza;
  ver D-16-02).
- Edición de `profile_template` tras la creación.

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Requisitos |
|----|----------|------------|
| D-16-01 | **`initial_budget_cents` opcional en `POST /api/departments`**: si viene, la MISMA transacción llama a `budgets.set_dept_budget` (validación Σ dept ≤ org reutilizada, sin duplicar reglas). Fallo de presupuesto → 422 y el departamento NO queda creado (rollback atómico). Auditoría: `department.created` + `budget.updated` (dos eventos, ambos reales). Respuesta `201 {department_id, name, budget_cents?}`. Sin el campo, comportamiento idéntico a spec 012. | REQ-ORG-121, REQ-BUD-002 |
| D-16-02 | **DELETE con reglas de negocio explícitas, no cascadas implícitas**: un departamento se puede eliminar SOLO si está "vacío". Bloqueos → `409 conflict` (razón + conteo en el mensaje): (a) usuarios asignados (`users.department_id`), (b) roles `dept_admin` scoped al depto (`user_roles.department_id`), (c) gasto histórico (`spend_ledger` — la atribución de gasto es dato de gobernanza inmutable), (d) agentes que referencian el depto (`agents.department_id`). Configuración huérfana que SÍ se elimina en cascada dentro de la misma transacción: budgets de nivel dept (cualquier status), policies de nivel dept, model_access de nivel dept. `budget_counters` no tiene FK y queda como histórico (solo existe si hubo gasto, y el gasto bloquea el DELETE). | constitution V, REQ-ORG-001 |
| D-16-03 | **Rename (`PUT /api/departments/{id}`)**: `name` 1–80 chars, único por tenant case-insensitive (duplicado → 422, mismo criterio que el alta). Audit `department.renamed` con payload `{old_name, new_name}`. Respuesta `200 {department_id, name}`. | REQ-ORG-121 |
| D-16-04 | **Permisos y aislamiento**: las tres mutaciones exigen `departments.manage` (org_admin en la matriz del catálogo; dept_admin → 403, ya verificado en spec 012 para POST). Todo corre dentro del transaction scope del tenant (`get_tx` + RLS): un id de OTRO tenant responde 404, nunca revela existencia (D-CP-09). MFA exigida por el router (igual que el resto de `/api`). | constitution III/IV, D-CP-09 |
| D-16-05 | **Audit**: `department.renamed` y `department.deleted` se dan de alta en `catalog.audit_actions` vía migración `0009_departments_crud` (encadenada sobre `0008_model_catalog_glm52`) Y en `db/schema.sql` §11 (misma sección embebida en la migración — paridad pg_dump, AC-DB-040). `department.deleted` lleva payload `{name, profile_template?, removed: {budgets, policies, model_access}}` para que el evento sea autosuficiente tras el borrado. | spec 008 §5, AC-DB-040 |
| D-16-06 | **UI honesta y completa** (`departments-page.tsx`): el diálogo "Add department" pasa de aviso estático a formulario real (nombre + budget mensual inicial opcional en USD); filas con acciones Budget / Rename / Policies / Delete; Delete usa `ConfirmDialog` con botón danger; todos los flujos con `loading`, toasts de éxito/error (el 409 del API se muestra con su mensaje real) y `overview.refetch()` al mutar. El comentario de cabecera del archivo se actualiza (ya no dice "sin API de directorio"). UI copy en inglés, montos en USD. | spec 009, constitution VI |

## 4. Requisitos y criterios de aceptación

### 4.1 API

**REQ-16-100** (M) — `POST /api/departments` con `{name, profile_template?,
initial_budget_cents?}`: sin budget se comporta como spec 012 (201
`{department_id, name}`); con budget crea depto + presupuesto atómicamente
(201 `{department_id, name, budget_cents}`); presupuesto inválido (sin org
budget, o Σ dept > org) → 422 y el depto NO existe.

**REQ-16-101** (M) — `PUT /api/departments/{id}`: rename válido → 200 +
audit `department.renamed` `{old_name, new_name}`; duplicado case-insensitive
→ 422; nombre vacío → 422; id inexistente o de otro tenant → 404; sin
`departments.manage` → 403.

**REQ-16-102** (M) — `DELETE /api/departments/{id}` de un depto vacío → 200
`{deleted: true, department_id, name}` + audit `department.deleted`; sus
budgets/policies/model_access de nivel dept desaparecen en la misma
transacción; el depto deja de listar en `GET /api/budgets/overview`.

**REQ-16-103** (M) — DELETE de un depto con usuarios → 409 con conteo y
guía ("move them first via PUT /api/users/{id}"); con roles dept_admin
scoped → 409; con gasto en `spend_ledger` → 409; con agentes → 409; id
inexistente/otro tenant → 404; sin `departments.manage` → 403.

**REQ-16-104** (M) — Aislamiento multi-tenant: ningún endpoint nuevo cruza
el scope (RLS activo en la suite con dos tenants sembrados; id forjado del
tenant B desde A → 404).

### 4.2 UI

**REQ-16-110** (M) — Crear depto desde la UI contra el API vivo: nombre +
budget opcional, toast de éxito, fila nueva visible; renombrar desde la
fila; eliminar con confirmación, toast, fila fuera de la lista; el 409 se
muestra al operador con el mensaje del API.

### 4.3 Convergencia

**REQ-16-120** (M) — Suite nueva `tests/test_departments_crud.py` en verde
sobre PostgreSQL real + suite existente sin regresiones + paridad
`db/tests` en verde. Evidencia en vivo: crear y eliminar un depto DESDE LA
UI del console contra el stack real, con `department.created`,
`department.renamed` y `department.deleted` registrados en `audit_log` del
tenant de evidencia.

## 5. Plan de validación

1. `pytest tests/test_departments_crud.py` + suite completa de
   `platform/tests` (isolated DB `trust_agent_platform_test`).
2. `pytest ../db/tests` (paridad schema↔migración, `TRUST_DB_CONTAINER=trust-agent-db`).
3. Migración aplicada al stack vivo (`docker compose run --rm migrate`) y
   smoke de las tres mutaciones por HTTP con sesión real.
4. Evidencia de UI por navegador contra `127.0.0.1:3100` (login real
   Keycloak password+OTP del tenant de evidencia) + verificación de audit
   por SQL.
