# Spec 038 — Estructura real de departamentos al nacer: catálogo expandido, nombres custom y wizard de onboarding

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* completada (decisión de producto t_c1829c09) → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-28 |
| **Autor** | product-owner (tarea t_c1829c09, feedback Miller "En los departamentos no estamos contemplando todos") |
| **Padre** | Investigación t_b76456e5 (repro live 12/12 PASS: NO es bug; causa = seed fijo de 4 + ningún flujo declara deptos reales). Specs afectadas: 004 (D-ONB-06 seed), 026 (self-signup, AC-26-001), 014 (Provider Console), 016 (CRUD departamentos — se reusa, sin cambios) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema (feedback Miller)

Toda organización nace con los mismos 4 departamentos seed en inglés (IT
Operations, Cloud Operations, Finance, Customer Operations — únicas 4
plantillas de `app/profiles/templates.py`) que no representan la estructura de
ninguna org real. Ningún flujo de nacimiento permite declarar los departamentos
reales: el self-signup ignora el campo `departments` (verificado en vivo),
el provisioning solo admite claves de plantilla del catálogo (`["Engineering"]`
→ 422), y el Provider Console solo ofrece checkboxes de las 4 plantillas. El
remedio actual ("Add department" a mano) existe pero Miller no llegó a
descubrirlo: **0 eventos `department.created` por CRUD en todo el sistema**.
La org de Miller es hispana: 4 nombres genéricos en inglés que no son su org
es la primera impresión del producto.

## 2. Decisión de producto (opciones evaluadas en t_c1829c09)

| Opción | Veredicto | Razón |
|--------|-----------|-------|
| **(a)** Self-signup con selección de departamentos | **Rechazada para el signup** (la capacidad de nombres custom SÍ entra, vía wizard y provisioning) | Spec 026 definió el signup mínimo a propósito; pre-activación el tenant está fail-closed 423 (cualquier selección quedaría guardada sin poder usarse); quien hace signup puede no ser quien mejor conoce la estructura completa. |
| **(b)** Ampliar el catálogo de plantillas | **ACEPTADA** (4 → 12, con display es+en) | Barato (metadata versionada, nada funcional lee `profile_template`); una org típica tiene Engineering/Sales/Marketing/HR/Legal…; da defaults sensatos al wizard. Solo no resuelve nada por sí misma: los nombres reales de la org deben poder ser custom. |
| **(c)** Wizard post-activación de departamentos | **ACEPTADA — núcleo de la decisión** | El momento correcto es el primer login del org_admin con el tenant activo: sirve a AMBOS caminos de nacimiento (self-signup y provisioning) y a las 10 orgs existentes — el remedio para la org de Miller es la misma feature, no un parche. |
| **(d)** Jerarquía/sub-departamentos | **DIFERIDA — fuera de alcance** | Sin evidencia de que Miller la pida ("todos" se interpreta como "todos los departamentos de mi org", no "sub-departamentos"). Pregunta abierta a Miller (§7). Si la pide → spec nueva (parent_id, cascadas, budgets anidados): impacto mayor, no se especula. |
| **(e)** Status quo + "usa Add department" | **RECHAZADA** | La evidencia demuestra que el cliente no descubre el botón (0 eventos CRUD en el sistema). La primera impresión no puede depender de que el cliente encuentre un remedio oculto. |

**Decisión**: **(c) + (b)** con nombres custom de primera clase en el wizard y
en el provisioning. El signup queda intacto (spec 026 no se toca en su forma;
solo se enmienda su AC de seed, ver §6).

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Requisitos |
|----|----------|------------|
| **D-38-01** | **Nacer representado, no nacer con estructura ajena**: se elimina el seed implícito de 4 departamentos. Una org nueva nace con (1) los departamentos explícitamente declarados en el provisioning, o (2) **CERO departamentos** (self-signup, o provisioning sin declaración). El `org_admin` nace con `department_id = NULL` (columna ya nullable, `db/schema.sql:435`; hoy se le asigna el primer depto seed, `provision.py:359`). Enmiendas con razón documentada: spec 004 D-ONB-06 y spec 026 AC-26-001 (§6). | REQ-DO-010, REQ-DO-011, REQ-DO-012 |
| **D-38-02** | **El momento es post-activación**: wizard "Organization structure" que se presenta UNA vez a cada `org_admin` al primer login con el tenant `active` y flag de onboarding pendiente. Skippable en un click. Re-abierto después desde la página Departments. El flag arranca `false` para TODOS los tenants (nuevos y los 10 existentes — la org de Miller ve el wizard en su próximo login y corrige su estructura). | REQ-DO-020, REQ-DO-023 |
| **D-38-03** | **Catálogo expandido 4 → 12 plantillas** con display es+en (precedente: catálogo legible es+en de spec 036). Claves nuevas: `engineering`, `sales`, `marketing`, `human_resources`, `legal`, `product`, `data_analytics`, `security_compliance` (más las 4 existentes). Cada una con skills/sugerencias coherentes (metadata versionada en `templates.py`, sin tabla nueva). | REQ-DO-001 |
| **D-38-04** | **Nombres custom de primera clase**: el nombre del departamento es free text del admin (1–80 chars, único case-insensitive por tenant — mismas reglas que POST spec 012 / rename D-16-03); `profile_template` queda como metadata opcional. Al elegir una plantilla en el wizard, el nombre por defecto es el display localizado pero **editable** (Miller puede crear "Finanzas" con template `finance`). Esto resuelve la fricción i18n sin romper la convención de UI copy en inglés: el nombre del depto es dato del cliente, no copy. | REQ-DO-021 |
| **D-38-05** | **Provisioning con estructura declarable**: `POST /api/admin/tenants` `departments` pasa de `list[str]` a `list[str \| {name, profile_template?}]` — string = clave de plantilla (backward compatible, nombre = `template.name`), objeto = departamento custom (template opcional validado contra el registro). Campo omitido o `[]` → org vacía + wizard (cambio de default, §6). Provider Console actualizado: selector del catálogo completo + nombres custom, sin checkboxes limitados a 4. | REQ-DO-011, REQ-DO-030 |
| **D-38-06** | **El wizard reusa el CRUD existente — cero endpoints de mutación nuevos**: crear/renombrar/eliminar viajan por `POST`/`PUT`/`DELETE /api/departments` (spec 012/016, reglas D-16-02/03 intactas, audit `department.created/renamed/deleted` existente). Superficie nueva mínima: (1) `GET /api/org/profile-templates` — catálogo con display es+en, read-only, cualquier usuario autenticado del tenant; (2) estado de onboarding por tenant (GET + PUT, solo `departments.manage`) — soporte exacto (campo en `tenants` vs tabla aparte) lo decide el plan. Audit del cierre de onboarding: acción nueva `onboarding.structure_completed` (INSERT-only en `catalog.audit_actions`, misma técnica que migración 0009) con payload `{source: "wizard"\|"skip", created, renamed, deleted}` — da al proveedor visibilidad de adopción. | REQ-DO-020, REQ-DO-021, REQ-DO-040 |
| **D-38-07** | **Wizard v1 = estructura, sin budgets**: los presupuestos por departamento quedan en la página Budgets con su validación Σ dept ≤ org (spec 013/016). Eliminar en el wizard solo deptos vacíos; el 409 de D-16-02 se muestra con su conteo y guía real, sin bypass. | REQ-DO-022 |
| **D-38-08** | **(d) jerarquía/sub-departamentos**: fuera de alcance, pregunta abierta a Miller (§7). No se añade `parent_id` ni nada de jerarquía en esta spec. | — |

## 4. Requisitos (EARS)

### 4.1 Catálogo de plantillas

**REQ-DO-001** (M) — Cuando un usuario autenticado solicita
`GET /api/org/profile-templates`, el sistema DEBE devolver las 12 plantillas
del registro con `{key, name, name_es, description, description_es,
suggested_monthly_budget_usd}`, ordenadas por `key`. Read-only, sin datos de
tenant.

**REQ-DO-002** (M) — Cuando se referencia un `profile_template` inexistente
en cualquier entrada (CRUD, provisioning, wizard), el sistema DEBE rechazar
con 422 listando las claves válidas (comportamiento existente de
`get_template`, se mantiene).

### 4.2 Nacimiento de la organización

**REQ-DO-010** (M) — Cuando se provisiona un tenant sin departamentos
declarados (campo omitido o `[]`), el sistema DEBE crear el tenant con CERO
departamentos y el `org_admin` con `department_id = NULL`, auditando
`tenant.provisioned`/`tenant.self_registered` como hoy (sin eventos
`department.created`).

**REQ-DO-011** (M) — Cuando se provisiona un tenant con `departments` mixto
`[claves de plantilla | objetos {name, profile_template?}]`, el sistema DEBE
crear exactamente esos departamentos en el mismo orden (string → nombre
`template.name` + template; objeto → nombre custom + template opcional),
auditando `department.created` por cada uno con payload
`{profile_template?}`; claves inválidas o nombres duplicados → 422 y rollback
total del provisioning.

**REQ-DO-012** (M) — Cuando una organización se crea por self-signup
(`POST /api/auth/signup-org`), el sistema DEBE crearla con CERO departamentos
(enmienda a AC-26-001, §6). El resto de spec 026 queda intacto.

### 4.3 Wizard de onboarding "Organization structure"

**REQ-DO-020** (M) — Cuando un `org_admin` autentica en un tenant `active`
cuyo flag de onboarding de estructura está pendiente, el Admin Console DEBE
presentar el wizard "Organization structure" una única vez, con las acciones:
(a) añadir desde plantillas del catálogo (nombre editable, default = display
localizado), (b) añadir con nombre custom sin plantilla, (c) renombrar
departamentos existentes, (d) eliminar departamentos vacíos. Skippable
("Skip for now") → cierra el onboarding sin mutar departamentos.

**REQ-DO-021** (M) — Cuando el wizard confirma cambios, el sistema DEBE
ejecutarlos vía el CRUD existente (`POST`/`PUT`/`DELETE /api/departments`)
con sus validaciones y audits actuales (spec 012/016), y cerrar el onboarding
auditando `onboarding.structure_completed` con payload
`{source, created, renamed, deleted}`.

**REQ-DO-022** (M) — Cuando el wizard intenta eliminar un departamento no
vacío (usuarios/roles dept_admin/gasto histórico/agentes, D-16-02), el
sistema DEBE mostrar el 409 real con conteo y guía ("move them first via
`PUT /api/users/{id}`"), sin bypass ni cascadas nuevas.

**REQ-DO-023** (M) — Cuando el onboarding ya está cerrado, el sistema DEBE
seguir ofreciendo el mismo flujo desde la página Departments (acción "Set up
structure"); y cuando la org tiene 0 departamentos, la página Departments y
el overview de budgets DEBEN mostrar empty-states con CTA al wizard (el
overview ya lista 0 deptos sin error — LEFT JOIN D-013-07).

**REQ-DO-024** (M) — Cuando el tenant está `pending_activation` o
`suspended`, el wizard NO se presenta (el gate 423/403 de spec 026/012 ya lo
bloquea todo; el wizard es estrictamente post-activación).

### 4.4 Provider Console

**REQ-DO-030** (M) — Cuando el proveedor crea un tenant desde el Provider
Console, el formulario DEBE permitir declarar departamentos: multi-select del
catálogo completo (12) + entrada de nombres custom, con preview de la
estructura resultante; sin declaración → aviso visible "the organization
starts empty; its admin sets up the structure on first login".

### 4.5 Permisos, aislamiento y audit

**REQ-DO-040** (M) — `GET /api/org/profile-templates` DEBE exigir sesión
válida del tenant (MFA según router `/api`) y no exponer ningún dato de otro
tenant; el estado de onboarding (GET/PUT) DEBE exigir `departments.manage`
(org_admin); un `dept_admin` o `user` → 403. Ningún endpoint nuevo cruza el
scope de tenant (D-CP-09).

**REQ-DO-041** (M) — La acción `onboarding.structure_completed` DEBE darse de
alta en `catalog.audit_actions` vía migración Alembic INSERT-only (convención
de serialización, regla 9 de CONTEXTO-TAREAS: `migration_guard.py new`,
anuncio previo en la card, paridad `db/schema.sql` §11) y aparecer en el
audit log del tenant con actor = el org_admin.

### 4.6 Convergencia

**REQ-DO-050** (M) — Suite nueva `tests/test_departments_onboarding.py` en
verde sobre PostgreSQL real + suite existente actualizada (tests que hoy
asumen 4 deptos seed se enmiendan) sin otras regresiones + paridad `db/tests`
en verde. Evidencia en vivo del journey completo (§5).

## 5. Criterios de aceptación

| AC | Criterio |
|----|----------|
| AC-38-001 | `GET /api/org/profile-templates` → 200 con 12 plantillas, campos es+en presentes; sin sesión → 401. |
| AC-38-002 | Provisioning sin `departments` → tenant con 0 deptos, admin `department_id` NULL, sin `department.created` en audit; provisioning `["finance", {"name":"Finanzas","profile_template":"finance"}, {"name":"Ventas"}]` → 3 deptos exactos en orden (`Finance`, `Finanzas`, `Ventas`) con sus audits; clave inválida o duplicado → 422 y sin tenant creado. |
| AC-38-003 | Self-signup → org `pending_activation` con 0 deptos (AC-26-001 enmendado); activación por el proveedor funciona sin cambios. |
| AC-38-004 | Primer login del org_admin post-activación → wizard visible; declarar p. ej. `Finanzas/Ventas/Marketing/Recursos Humanos` (2 con template, 2 custom) → overview de budgets lista EXACTAMENTE esos 4 deptos (LEFT JOIN, sin budgets); audit: 4× `department.created` + 1× `onboarding.structure_completed` `{source:"wizard", created:4, …}`. |
| AC-38-005 | Skip del wizard → sin mutaciones, audit `onboarding.structure_completed` `{source:"skip", …}`; el wizard NO vuelve a aparecer en el siguiente login; la página Departments ofrece "Set up structure". |
| AC-38-006 | Wizard sobre org existente con depto seed con usuarios asignados: intentar eliminarlo → 409 visible con conteo y guía (sin bypass); renombrar funciona (audit `department.renamed`). |
| AC-38-007 | Org con 0 deptos: Departments y budgets overview muestran empty-state con CTA; overview NO responde error (D-013-07 con lista vacía). |
| AC-38-008 | Provider Console: provisioning con selección mixta (plantillas + custom) desde la UI → tenant con esa estructura exacta; sin selección → aviso visible y org vacía. |
| AC-38-009 | `dept_admin`/`user` en PUT de estado de onboarding → 403; id de tenant forjado → 404/403 según superficie (D-CP-09); tenant pending → wizard inalcanzable (423). |
| AC-38-010 | Journey E2E con evidencia real contra el stack vivo: self-signup → activación → primer login → wizard → estructura real en español visible en overview; y un tenant existente (pre-spec) ve el wizard una vez en su próximo login. |

## 6. Enmiendas a specs existentes (razón documentada, constitution II)

| Spec | Cambio | Razón |
|------|--------|-------|
| **004 D-ONB-06** | "departamentos seed" deja de ser parte del nacimiento implícito: el provisioning crea los deptos DECLARADOS o ninguno (D-38-01/05). | El seed fijo de 4 es la causa raíz verificada del feedback Miller: ninguna org real coincide con esos 4 deptos genéricos en inglés, y el único uso funcional del seed (asignar `first_dept_id` al admin) es innecesario (`department_id` nullable). |
| **026 AC-26-001 / REQ-SS-001** | "4 deptos seed" → "0 deptos; estructura declarada post-activación (spec 038)". | Ídem. El signup sigue mínimo a propósito — la decisión (a) fue rechazada para no tocar ese diseño. |
| **014 Provider Console** | Checkboxes de 4 plantillas → selector de catálogo completo + custom (REQ-DO-030). | El formulario actual no puede declarar la estructura real; `["Engineering"]` → 422 hoy. |
| **016** | Sin cambios — el CRUD se reusa íntegro (D-38-06). | — |
| **013 D-013-07** | Sin cambios — el overview LEFT JOIN ya lista todos los deptos con y sin budget, incluida la lista vacía. | Verificado en t_b76456e5. |

`DEFAULT_TEMPLATE_KEYS` / `resolve_template_keys` pierden su rol de default
implícito (quedan como lista de sugerencias del catálogo); los tests que hoy
asumen el seed de 4 se enmiendan en la fase implement.

## 7. Pregunta abierta para Miller (no bloquea esta spec)

> "¿Necesitas que un departamento tenga sub-departamentos (p. ej. Ingeniería →
> Plataforma / Mobile), o te basta la lista plana de departamentos?"

Si pide jerarquía → spec nueva (parent_id en `departments`, cascadas de
D-16-02, budgets anidados, RLS): NO se especula en 038 (D-38-08).

## 8. Respuesta sugerida a Miller

> Tenías razón: la plataforma nacía con 4 departamentos genéricos de ejemplo
> y no ofrecía ningún momento para declarar los tuyos. Lo corregimos de raíz:
> (1) las organizaciones nuevas ya no nacen con departamentos de ejemplo —
> nacen vacías o con la estructura que declare quien las cree; (2) en tu
> primer inicio de sesión te aparece un asistente de "estructura de la
> organización" donde declaras tus departamentos reales — con plantillas
> sugeridas (12, en español) o con tus propios nombres, por ejemplo
> "Finanzas" o "Recursos Humanos"; (3) tu organización actual verá ese
> asistente en tu próximo inicio de sesión para que la corrijas en un minuto.
> Pregunta: ¿necesitas sub-departamentos o te basta la lista plana?

## 9. Plan de validación

1. `pytest tests/test_departments_onboarding.py` + suite completa de
   `platform/tests` (seed amendments) + `pytest ../db/tests` (paridad).
2. Migración INSERT-only aplicada al stack vivo (`docker compose run --rm
   migrate` + rebuild de imagen `migrate`, regla 9).
3. Evidencia de UI por navegador contra `127.0.0.1:3100`: journey AC-38-010
   completo con sesión real (PKCE + TOTP) y verificación de audit por SQL.

## 10. Fuera de alcance

- Jerarquía/sub-departamentos (D-38-08, §7).
- Presupuestos dentro del wizard (D-38-07).
- Edición de `profile_template` tras la creación (sigue fuera de alcance como
  en spec 016).
- Reasignación masiva de usuarios al eliminar deptos (sigue el 409 con guía
  de spec 016).
- i18n completa del Admin Console (solo display es+en del catálogo y nombres
  custom del cliente).
