# Spec 033 — Policies: página dedicada unificada del Admin Console (mockup Miller, post-tag v0.1.0)

| Campo | Valor |
|---|---|
| **Estado** | Fase *converge* (SDD) — implementada y verificada en t_e03bba6c (2026-10-27); evidencia: `docs/evidencia/policies-unified-console/evidencia-e2e.md` |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-27 |
| **Autor** | ui (tarea t_e03bba6c) |
| **Padre** | specs/009-admin-console (REQ-AC-011: edición distribuida ya entregada), specs/005-governance (REQ-GOV-020…024, combinator deny-overrides), specs/029-policy-catalog-v2 (catálogo tipado 18+29 keys) |
| **Nota de trazabilidad** | La card referencia "Spec 031": esa spec es el enforcement batch 2 (plugin); esta página es la superficie de CONSULTA/edición del catálogo completo que esas lanes pueblan y enforcement (030/031) aplica. Los requisitos de UI viven AQUÍ. |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD (montos) |

---

## 1. Problema (decisión Miller)

Para el piloto las policies se editan distribuidas (Organization para org, Departments
para deptos, Users para usuarios). Miller pidió además la página DEDICADA del mockup
original: **una sola vista unificada de TODAS las policies por nivel** (org → deptos →
usuarios) con tabla maestra, origen (default vs override), edición inline por nivel y
el **diff de efectivos por usuario** (qué hereda, qué estrecha, qué niega). Post-tag
v0.1.0: no bloquea el piloto.

Hoy la API ofrece `GET /api/policies?level=…` (un nivel por llamada) y
`GET /api/me/policies` (efectiva SOLO del caller). Faltan dos piezas READ-ONLY para que
la página exista sin simular nada: el **catálogo tipado** (keys, tipos, defaults,
invariables — hoy solo en DB) y la **efectiva de un usuario arbitrario con
provenance por key** (hoy solo del propio principal).

## 2. Alcance

**Dentro de alcance**:

1. **Backend read-only** (control plane, `platform/app/governance/`):
   - `GET /api/policies/matrix` — catálogo completo (`key`, `value_type`,
     `invariable`, `default_value`, `choices` para enums) + valores org + overrides
     por departamento + overrides por usuario (solo usuarios CON overrides), con
     nombres para render. `org_admin`: todo; `dept_admin`: sus departamentos
     (baseline org visible read-only, precedente `GET /api/budgets/overview`).
   - `GET /api/policies/effective?user_id=<uuid>` — efectiva deny-overrides de un
     usuario arbitrario + provenance por key: valores de cada nivel, efectiva,
     `source` (default/org/dept/user), `tightened_by_dept`, `tightened_by_user`,
     `denied`. Mismo alcance de roles que matrix.
   - Cero mutaciones nuevas: la edición usa los PUT/DELETE existentes
     (`REQ-GOV-021…023`, validación 422 y no-relajación intactas).
2. **Frontend** — página `/policies` del Admin Console (nav "Policies"):
   - **Tabla maestra**: una fila por key del catálogo con default, valor org,
     valor del nivel seleccionado y efectiva del scope; badges de origen
     (Default / Org / Dept / User); edición inline por tipo (boolean, integer,
     enum, list, map) y "Reset to inherited" (DELETE) con confirmación.
   - **Diff de efectivos por usuario**: picker de usuario (directorio real) →
     tabla por key con heredado/estrechado por depto/estrechado por
     usuario/negado, resumen con conteos.
   - Estados reales: loading (skeleton), error (retry), 403 (empty state),
     422 inline con el mensaje real del control plane.
3. **Evidencia real** en `docs/`: E2E contra el stack vivo (login PKCE+TOTP,
    matrix real, edición inline real con 422 de no-relajación, diff real).

**Fuera de alcance**:

- Cambios de semántica del combinator o de las validaciones (specs 005/029).
- Edición masiva/bulk, historial de versiones por key en la página (el audit log
  ya lo registra; la página muestra la versión vigente).
- Model approvals (viven en Model Access, spec 018).

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Razón |
|----|----------|-------|
| **D-33-01** | **Dos endpoints read-only, cero mutaciones nuevas**: `matrix` y `effective` solo leen (`policies`, `catalog.policy_keys`, `users`, `departments`); toda escritura sigue por los PUT/DELETE existentes con su validación y audit. La página NUNCA re-implementa el combinator: la efectiva y los flags de tightening los computa el backend con `combinator.combine` (una sola fuente de verdad). | Duplicar semántica deny-overrides client-side sería una simulación esperable de divergir (constitution I). |
| **D-33-02** | **Provenance por key computado con el combinator puro**: para cada key se computan `v_org = combine(org,∅,∅)`, `v_org_dept = combine(org,dept,∅)` y `v_full = combine(org,dept,user)`; `tightened_by_dept = (key en dept) ∧ v_org_dept ≠ v_org`; `tightened_by_user = (key en user) ∧ v_full ≠ v_org_dept` (comparación canónica orden-insensible, mismo `_canonical` del upsert). `source` = el nivel más bajo con valor explícito. | Reutiliza la semántica exacta de REQ-POL-007; un override que NO cambia la efectiva (p. ej. mismo valor que el superior) no se reporta como tightening. |
| **D-33-03** | **`denied` derivado, no inventado**: boolean `allow_*` efectivo `false`, lista `allowed_*` efectiva `[]`, y `web_domain_policy` efectivo `allowlist` con dominios `[]` → `denied=true`. `require_*` true NO es "denied" (es un piso activo, se muestra como tal). | "Qué niega" debe ser un hecho del valor efectivo, no una interpretación de UI. |
| **D-33-04** | **Alcance por rol espejo de budgets**: `org_admin` ve la matriz completa; `dept_admin` ve sus departamentos y los usuarios de ellos (baseline org read-only); otros roles → 403 (empty state explicativo). La edición por nivel la siguen gateando los PUT/DELETE existentes (`policies.org.manage` / `policies.dept.manage`). | Precedente `GET /api/budgets/overview` (dept_admin scoped); sin scope oracle (404/403 según patrón existente). |
| **D-33-05** | **Enums del catálogo viajan en el endpoint**: `choices` por key (desde `STRING_RANKS` + `WEB_DOMAIN_MODES`) para que la UI renderice selects reales en vez de inputs libres; keys sin enum → `null`. La UI valida formato client-side solo como UX; la verdad es el 422 del control plane. | Evita errores de tipeo en keys de enum (approval_mode, unbind_mode, web_domain_policy.mode). |
| **D-33-06** | **Página dedicada, edición distribuida intacta**: la página no reemplaza Organization/Departments/Users; convive. Nav "Policies" tras "Model Access" (bloque GOVERNANCE del sidebar). Invariables renderizados con candado, no editables (el backend los rechaza en cualquier nivel, REQ-POL-004). | Decisión Miller: distribuida para el piloto, dedicada post-tag. |
| **D-33-07** | **Estándar UI Pro**: design system existente (Card/Badge/Table/Dialog/ConfirmDialog/toasts/skeletons), lucide-react, dark tokens, responsive, accesible (`role="tablist"`, labels, aria). UI copy en inglés. | Mandato Miller / spec 009. |

## 4. Requisitos (EARS)

Trazabilidad: serie **REQ-033**. Padre: specs 005/009/029.

**REQ-033-001** (M) [D-33-01] — Cuando un principal con `policies.org.manage` o
`policies.dept.manage` llama `GET /api/policies/matrix`, el sistema DEBE responder
el catálogo tipado completo, los valores org activos, y por cada departamento y
usuario-con-overrides sus valores activos, con nombres legibles; un `dept_admin`
DEBE recibir solo sus departamentos y los usuarios de ellos; un rol sin esos
permisos DEBE recibir 403.
> **AC-033-001**: (API) org_admin → 200 con `catalog` (≥ 47 keys: 18 + 29),
> `org`, `departments[]` (con `name` y `policies`), `users[]` (solo con overrides
> activos, con `email`/`department_name`); dept_admin → 200 sin departamentos fuera
> de scope; usuario sin permisos → 403.

**REQ-033-002** (M) [D-33-01/02] — Cuando un principal autorizado llama
`GET /api/policies/effective?user_id=<uuid>` de un usuario de su alcance, el
sistema DEBE responder, por CADA key del catálogo: `default_value`, valores
`org`/`dept`/`user` (null si el nivel no la fijó), `effective` (deny-overrides real),
`source`, `tightened_by_dept`, `tightened_by_user` y `denied` (D-33-03); un usuario
fuera de alcance del dept_admin DEBE responder 404/403 sin oráculo.
> **AC-033-002**: (API) usuario con org `[vmware,email]` + dept `[vmware]` +
> user `[vmware]` → `allowed_connectors.effective == ["vmware"]`,
> `source == "user"`, tightened por dept y por user; key sin overrides →
> `source == "default"`, flags false; `allow_*` false → `denied == true`.

**REQ-033-003** (M) [D-33-06] — Cuando un admin navega a `/policies`, la página
DEBE mostrar la tabla maestra (una fila por key del catálogo) con default, valor
org, valor del nivel seleccionado (org/departamento/usuario) y efectiva del scope,
badges de origen, y edición inline por tipo con los PUT existentes; "Reset to
inherited" DEBE usar el DELETE existente con diálogo de confirmación; las keys
invariables DEBEN mostrarse no editables.
> **AC-033-003**: (E2E) editar inline un boolean en org → toast de éxito y valor
> persistido (refetch real); valor que relaja (REQ-POL-007) → 422 con el mensaje
> real del control plane mostrado inline; reset de un override de depto → el valor
> vuelve al del nivel superior.

**REQ-033-004** (M) [D-33-02/03] — Cuando un admin selecciona un usuario del
directorio en la pestaña "Effective by user", la página DEBE mostrar el diff por
key: qué hereda (sin overrides), qué estrecha el departamento, qué estrecha el
usuario y qué queda negado, con conteos resumidos — todo computado por el backend
(REQ-033-002), nunca re-derivado client-side.
> **AC-033-004**: (E2E) usuario con overrides reales en los 3 niveles → badges
> correctos por key y conteos coincidentes con la respuesta de la API.

**REQ-033-005** (M) [D-33-07] — La página DEBE manejar estados reales: loading
(skeleton), error de red (retry), 403 (empty state explicativo por rol) y 422
(mensaje real inline), y DEBE compilar limpia (`npm run build`).
> **AC-033-005**: (build) `npm run build` exit code 0 sin errores nuevos;
> (unit API) suite de platform pasa.

**REQ-033-006** (M) [transversal] — Cero simulación: la página no debe contener
datos mock presentados como reales; todo render proviene de respuestas de la API
del control plane.
> **AC-033-006**: (review) grep de la página sin literales de datos de policies;
> E2E contra el stack vivo documentado en `docs/`.

## 5. Amenazas y límites

- `matrix` es una lectura agregada (N+1 por depto/usuario con overrides): aceptable
  en el piloto (decenas de entidades); si escala, una sola query con
  `jsonb_object_agg` (tarea futura del perfil dev).
- La efectiva mostrada es un snapshot de consulta; el bundle JWS firmado sigue
  siendo la verdad en el device (spec 019).
- dept_admin ve la baseline org (valores, no edición): ya ve efectos combinados vía
  budgets/overview; no expone nada que no vea ya.

## 6. Trazabilidad

| Req | Padre | Componente |
|---|---|---|
| REQ-033-001 | REQ-GOV-020, D-018-02 | `platform/app/governance/policies_matrix.py` + `router.py` |
| REQ-033-002 | REQ-POL-002/006/007 | `policies_matrix.py` (combinator puro) |
| REQ-033-003 | REQ-AC-011, REQ-POL-003/004/007 | `console/src/app/(console)/policies/` |
| REQ-033-004 | REQ-POL-006 | ídem (pestaña Effective by user) |
| REQ-033-005 | spec 009 | ídem + `npm run build` |
| REQ-033-006 | constitution I | ídem + docs de evidencia |

## 7. Convergencia (verificación real, 2026-10-27)

- **AC-033-001** ✅ `test_policies_matrix.py::test_matrix_org_admin_sees_everything`
  (catálogo ≥ 47 keys con choices, org, deptos con nombre, usuarios con overrides) y
  `test_matrix_scoping_and_permission` (dept_admin scoped; `audit.view` limitado).
- **AC-033-002** ✅ `test_effective_diff_provenance` (source/tightened/denied por key,
  estado auto-contenido en los 3 niveles) y `test_effective_diff_scope_and_validation`
  (404 sin oráculo para dept_admin fuera de scope; 422 UUID malformado).
- **AC-033-003** ✅ E2E real: edición inline org (`allow_voice_mode` → PUT → toast →
  fila con badge Org), 422 de no-relajación mostrado inline con el mensaje real del
  control plane (`approval_mode` org=manual vs dept=off), Reset con ConfirmDialog →
  `is_active=f` en DB. Invariables con candado, no editables.
- **AC-033-004** ✅ E2E real: diff del admin (43 inherited / 18 denied) y tightening
  user-level `session_timeout_minutes=15` → badge `Tightened · user` y contador 1;
  todo computado por `GET /api/policies/effective`.
- **AC-033-005** ✅ `npm run build` exit 0 (22 rutas, `/policies`), `npm run lint`
  limpio; suite backend: 4/4 nuevos tests, 641 passed en total (los 4 fallos de
  `test_provision.py` son pre-existentes: reproducidos idénticos sin esta tarea).
- **AC-033-006** ✅ Sin datos mock en la página (todo render proviene de
  `/api/policies/matrix`, `/api/policies/effective`, `/api/users`); evidencia E2E
  con login PKCE+TOTP real en `docs/evidencia/policies-unified-console/`.

QA visual (deepseek-v4.1-flash, skill vision-check-maas): checks estructurales
C1/C2/C5 PASS; C3 corregido (wrapper `overflow-x-auto`); pulido aplicado según
crítica (labels, contraste, pills semánticos, selector sin duplicar, filtro de
keys). Score final ≈ 6.5/10 (juez no determinístico ±1–1.5).
