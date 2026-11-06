# Spec 041 — Policies v2: políticas nombradas generales con sidebar de capacidades on/off, asignables a org/depto/usuario (visión Miller)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* (SDD) — mapping al engine decidido, contrato del builder validado contra la DB viva; implementación encargada a dev/ui/trust-e2e (tareas hijas) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-29 |
| **Autor** | product-owner (tarea t_4e764fcf) |
| **Padre** | specs/036-policies-ux (catálogo legible 47 keys + panel por key), specs/033-policies-unified-console (matrix/effective), specs/029-policy-catalog-v2 (catálogo tipado), specs/005-governance (deny-overrides, REQ-POL-003/004/007), specs/009-admin-console |
| **Idioma** | Español (docs) / bilingüe es+en (contrato del builder) / inglés (código y UI copy) |

---

## 1. Problema (repro de Miller, rediseño #2)

> "Yo me imagino que cuando creo una política es una política GENERAL. Dentro de
> esa política voy añadiendo las funcionalidades. Como un SIDEBAR de opciones
> que voy metiendo a la política, deshabilitando o habilitando opciones."

La spec 036 convergió un panel legible **por key individual** con asignador de
alcance: funcional, pero la unidad mental de Miller es la **POLÍTICA NOMBRADA**
(modelo IAM-style): el admin crea "Política de practicantes", dentro activa y
desactiva capacidades (buscar internet, terminal, email, modelos, MFA,
conexiones…) desde un sidebar, y luego **asigna esa política** a la
organización, un departamento o un usuario. La key individual deja de ser la
unidad de creación; pasa a ser un detalle interno del bundle.

## 2. Alcance

**Dentro de alcance**:

1. **Modelo de política nombrada** (este documento lo define COMPLETO): una
   política nombrada = `name` + `description` + `definition` (mapa `key →
   value` sobre las 44 keys no invariables del catálogo tipado). El contrato
   `named-policy-builder.json` (junto a esta spec) define los 6 grupos del
   sidebar, las 47 capacidades (items) mapeadas 1:1 sobre esas keys, sus
   defaults y las 3 plantillas pre-cargadas. **Validado contra el estado real**
   el 2026-10-29: cobertura 44/44 keys no invariables, items de `allowed_tools`
   == `known_items`, tipos de los defaults y de las plantillas == `value_type`
   de `catalog.policy_keys` (DB viva), `org_only` == `ORG_ONLY_KEYS`,
   invariables excluidas.
2. **Backend (dev)**: tablas `named_policies` + `named_policy_assignments`
   (migración Alembic), CRUD de políticas nombradas, preview dry-run,
   asignación/atómica materialización vía `policies.upsert` existente,
   unassign, reapply, vista de destinatarios, seed de plantillas en
   provisioning + backfill. Cero cambios de semántica al engine.
3. **Frontend (ui)**: Policy Builder en `/policies` — (a) lista de políticas
   nombradas con "+ Nueva política"; (b) editor con nombre + descripción +
   SIDEBAR de capacidades por grupo con toggles; (c) asignación a
   Organización / Departamento / Usuario con preview de diff y vista de qué
   políticas aplican a cada destinatario. El builder pasa a ser la vista por
   defecto; el panel legible de spec 036 queda como pestaña secundaria.
4. **E2E (trust-e2e/qa)**: escenario "Practicantes" en vivo — crear la
   política (sin internet, sin terminal, solo el modelo aprobado elegido),
   asignarla a un usuario y verificar en su Hermes enrolado que cumple EXACTO.

**Fuera de alcance**:

- Cualquier cambio al combinator deny-overrides, la validación de upsert (422),
  la no-relajación (REQ-POL-007), el audit existente o el bundle JWS (specs
  005/029/019 intactas). El engine NO se reescribe — se mapea.
- Nuevas policy keys (el catálogo tipado sigue siendo el de la DB).
- Herencia condicional/parametrizada entre políticas nombradas (una política
  no hereda de otra), prioridades entre políticas concurrentes más allá de lo
  que dicta el engine, y historial de versiones de políticas nombradas.
- Asignación a roles/grupos (no existen todavía como destinatarios de policy).

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Razón |
|----|----------|-------|
| **D-41-01** | **Una política nombrada ES un preset de policy keys; la asignación MATERIALIZA**. Asignar "Practicantes" al depto X = ejecutar `policies.upsert` (función existente, sin tocar) por cada key de `definition` a nivel depto/X. La tabla `policies` sigue siendo la ÚNICA verdad para combinator, matrix, effective y bundle JWS: el desktop no sabe que existen políticas nombradas. Desasignar = `policies.deactivate` de las keys que esa asignación escribió. | El engine no se reescribe (mandato); deny-overrides, 422, no-relajación y audit quedan intactos GRATIS porque el camino de escritura es el mismo. Cero superficie de bypass nueva. |
| **D-41-02** | **El contrato del builder vive en el BACKEND como paquete de datos** (`platform/app/governance/named_policy_builder.json`, copia byte-a-byte del contrato de esta spec, test sha256) y viaja al frontend SOLO dentro del bloque `builder` de `GET /api/named-policies`. La UI JAMÁS hardcodea grupos, capacidades, defaults ni plantillas. Un test de plataforma exige cobertura 1:1: toda key no invariable del catálogo alcanzable por ítem, e ítems de `allowed_tools` == `known_items`. | Mismo argumento que D-36-01: fuente única de verdad; el drift se convierte en fallo de CI, no en desincronización silenciosa. |
| **D-41-03** | **Sidebar de 6 grupos**: los 5 que nombró Miller (Navegación y web, Sistema y terminal, Comunicaciones, Modelos, Seguridad) + **"Agente y automatización"** (skills, plugins, MCP, memoria, personalidad, tareas, modos automáticos) para las capacidades de agente que no caben en los 5 sin inflar un grupo a 19 ítems. Cada capacidad es un **ítem** de dos tipos: `list-item` (un ítem de una key lista, p. ej. "Búsqueda en internet" = `allowed_tools:web_search`; toggle ON/OFF = incluir/excluir el ítem del valor compuesto) o `scalar` (una key entera con su control legible de spec 036, p. ej. "Terminal" = `allowed_terminal_backends` con sub-toggles docker/local/ssh). **Regla de composición**: una key lista se escribe en la `definition` si y solo si ≥1 de sus ítems está ON, con valor = ítems ON; una key escalar se escribe si su ítem está ON, con el valor elegido (preseleccionado = `default_value` del contrato). Al activar el PRIMER ítem de una key, los ítems hermanos toman su `default_on` del contrato (p. ej. activar "Búsqueda en internet" deja `allowed_tools` en el valor por defecto del catálogo; el admin poda después). Ítems `requires_choice` (p. ej. "Modelos de IA disponibles", default null) bloquean el guardado hasta que el admin elija valor. | Es la metáfora exacta de Miller ("voy metiendo opciones, habilitando/desabilitando"), generalizada a las 44 keys sin inventar semántica nueva: el resultado siempre es un mapa `key→value` que el engine ya entiende. |
| **D-41-04** | **Asignación atómica con preview dry-run**. `POST /api/named-policies/{id}/assign` ejecuta TODOS los upserts en UNA transacción: si cualquier key falla validación (422 de no-relajación, valor inválido), la asignación entera se rechaza con detalle por key y NADA se escribe. Antes de confirmar, la UI llama a `GET /api/named-policies/{id}/preview?level=&department_id=&user_id=` que computa el mismo análisis SIN escribir: keys a escribir (con valor actual del nivel si ya existe → marca "reemplaza"), keys que relajarían (→ la asignación fallará), keys `org_only` cuando el destino no es org (→ se omiten, con aviso explícito en el resultado y en el preview). | El admin ve EXACTAMENTE qué va a pasar antes de que pase; el todo-o-nada evita medias políticas aplicadas (una política a medias es una mentira de seguridad). Las `org_only` no se pueden fijar a nivel depto/usuario (el engine las rechaza, D-AUTH-07/08): omitirlas con aviso es lo único honesto. |
| **D-41-05** | **Ownership por snapshot**. Cada asignación guarda `applied_keys` (el mapa `key→value` efectivamente escrito). Desasignar desactiva (`policies.deactivate`) SOLO las keys cuyo valor actual en el nivel sigue siendo el del snapshot; una key que el admin haya editado a mano después de la asignación NO se toca (se conserva el valor manual y se reporta en la respuesta). Una key sobreescrita después por OTRA política nombrada tampoco se toca (su valor ya no es el del snapshot). | Desasignar no debe destruir trabajo manual explícito ni la propiedad de otra política; el snapshot hace la unassign segura sin añadir columnas de ownership a la tabla `policies` del engine. |
| **D-41-06** | **Editar una política NO auto-propaga**. Al guardar una edición, las asignaciones existentes quedan marcadas `stale` (`policy.updated_at > assignment.applied_at`); la UI las muestra con badge "Desactualizada" y un botón "Aplicar cambios". `POST /api/named-policies/{id}/reapply` re-materializa best-effort POR asignación: cada destinatario es atómico, los fallos (p. ej. una edición que ahora relajaría un nivel superior) se reportan por destinatario con el 422 real, y las asignaciones exitosas refrescan su snapshot. | Auto-propagar silencioso puede romper N destinatarios con un solo save (y esconder 422s); no propagar deja drift invisible. El modelo explícito (badge + un clic + reporte por destinatario) es IAM-style y auditable. |
| **D-41-07** | **Borrado con asignaciones activas → 409** con la lista de destinatarios; la UI ofrece "Desasignar en todas partes y borrar" (unassign all + delete, explícito). Sin asignaciones → borrado directo. | Borrar una política aplicada sin más dejaría las keys materializadas huérfanas e irreversibles desde el builder; el 409 obliga a la decisión consciente. |
| **D-41-08** | **Plantillas = filas `named_policies` sembradas**, con `source_template = 'builtin:<slug>'` (badge "Plantilla" en la UI). Se siembran idempotentemente en el provisioning de tenant (`platform/app/tenants/provision.py`) y por migración de backfill para los tenants existentes. Son políticas COMUNES: editables, asignables y borrables como cualquier otra; "Nueva política desde plantilla" COPIA la `definition` a una política nueva (la plantilla queda intacta). Las 3 plantillas (`solo-lectura-web`, `desarrollador`, `restringido-total`) están definidas con valores concretos en el contrato. | "El admin parte de una y ajusta" (mandato). Reusar la tabla evita un segundo modelo de datos para lo mismo; el slug permite re-sembrar sin duplicar. |
| **D-41-09** | **Permisos espejo de los existentes, por destinatario**: asignar a la organización exige `policies.org.manage`; a un departamento, `policies.dept.manage` sobre ese depto (u org); a un usuario, `policies.dept.manage` sobre el depto del usuario (u org) — la MISMA regla que los PUT de nivel en `governance/router.py` hoy. Crear/editar/borrar políticas nombradas exige `policies.org.manage` o `policies.dept.manage`; ver el builder, cualquiera de los dos. | Cero superficie de bypass: quien puede escribir una key a un nivel por el panel 036 puede hacerlo vía política nombrada; quien no, no. |
| **D-41-10** | **El builder es la vista por defecto de `/policies`** (pestaña "Políticas"); el panel legible por key de spec 036 queda como pestaña "Ajustes individuales" (intacto); la "Vista técnica" de spec 033 sigue solo para `org_admin`. `GET /api/policies/matrix`, `GET /api/policies/effective` y el bundle JWS NO cambian de contrato. | El modelo mental de Miller manda el default; el panel por key sigue siendo la herramienta de ajuste fino que ya convergió (coste cero, no se borra nada). |
| **D-41-11** | **Todo audita**: `named_policy.created/updated/deleted/assigned/unassigned/reapplied` vía `audit.emit` existente, con payload `{named_policy_id, name, level, department_id, user_id, keys[]}`. | Constitution V: cada capacidad gobernable y auditada. El audit log es append-only ya existente. |
| **D-41-12** | **Vista de destinatarios desde las asignaciones, no re-derivada**: `GET /api/named-policies/recipients` devuelve, para org/deptos/usuarios del alcance del admin, la lista de políticas nombradas asignadas (nombre + badge stale) leyendo `named_policy_assignments`. La efectiva por key sigue viniendo SOLO de `matrix`/`effective` (D-36-04 se mantiene). | La UI no re-implementa nada; "qué políticas aplican a este destinatario" es un hecho de asignación, no un cálculo de combinación. |

## 4. Requisitos (EARS)

Trazabilidad: serie **REQ-041**. Padre: specs 005/029/033/036.

### Modelo y CRUD

**REQ-041-001** (M) [D-41-01] (política nombrada = preset de keys) — Cuando un
admin con `policies.org.manage` o `policies.dept.manage` crea una política
nombrada (`POST /api/named-policies` con `name`, `description?`,
`definition`, `template_slug?`), el sistema DEBE validar que: (a) `name` no
vacío y único por tenant; (b) cada key de `definition` existe en
`catalog.policy_keys` y NO es invariable (si no → 422 con la lista de keys
inválidas); (c) cada valor pasa la MISMA validación de `value_type` que el
upsert por key (`_validate_value`); y DEBE almacenarla en `named_policies`
con `tenant_id`, auditando `named_policy.created`. Con `template_slug` DEBE
copiar la `definition` de la plantilla como punto de partida.
> **AC-041-001**: (API+test) crear política válida → 201 con id; nombre
> duplicado → 422; key invariable (`approved_models_only`) en definition →
> 422 con mensaje; valor de tipo incorrecto (`session_timeout_minutes:
> "abc"`) → 422; política creada por template copia la definition exacta;
> fila en `named_policies` con el `tenant_id` del actor; evento
> `named_policy.created` en `audit_log`.

**REQ-041-002** (M) [D-41-01] (CRUD + borrado con 409) — Cuando un admin
edita (`PUT /api/named-policies/{id}`: `name?`, `description?`,
`definition?`) o borra (`DELETE`) una política nombrada, el sistema DEBE
aplicar las mismas validaciones de REQ-041-001, auditar
`named_policy.updated/deleted`, y RECHAZAR el borrado con 409 (lista de
asignaciones activas con destinatario) mientras tenga asignaciones; el
sistema DEBE ofrecer `DELETE ?cascade=unassign` que desasigna todas (REQ-041-005)
y luego borra, auditando cada unassign.
> **AC-041-002**: (API+test) editar definition → 200 y las asignaciones
> existentes quedan `stale` (ver AC-041-006); borrar con 1 asignación → 409
> con `{assignments: [{level, department_id, user_id}]}`; borrar con
> `cascade=unassign` → 204, keys del snapshot desactivadas, eventos
> `named_policy.unassigned` + `named_policy.deleted`; borrar sin asignaciones
> → 204 directo.

### Contrato del builder

**REQ-041-003** (M) [D-41-02/03] (sidebar servido por el backend) — Cuando el
control plane sirve `GET /api/named-policies`, el sistema DEBE incluir el
bloque `builder` con los 6 grupos, las 47 capacidades (ítems) y las 3
plantillas del contrato `specs/041-named-policies/named-policy-builder.json`
(copia byte-a-byte en `platform/app/governance/named_policy_builder.json`),
y la cobertura DEBE ser 1:1: toda key no invariable del catálogo alcanzable
por al menos un ítem, ítems `list-item` de `allowed_tools` == `known_items`
del catálogo legible, ítems `org_only` == `ORG_ONLY_KEYS`.
> **AC-041-003**: (API+test) test de plataforma: sha256 del JSON de plataforma
> == sha256 del contrato de la spec; cobertura 44/44 keys no invariables;
> `allowed_tools` ítems == known_items (4/4); `require_mfa` y
> `allow_self_registration` marcados `org_only`; `GET /api/named-policies`
> expone `builder` a quien tiene permiso de ver policies.

### Asignación

**REQ-041-004** (M) [D-41-04] (preview dry-run) — Cuando el admin elige un
destinatario para una política (`GET /api/named-policies/{id}/preview?level=
org|dept|user&department_id=&user_id=`), el sistema DEBE devolver SIN
escribir: por cada key de la definition → `{key, new_value, current_value?,
overwrites: bool}`, la lista `skipped_org_only` (keys org_only cuando el
destino no es org), y la lista `would_relax` (keys cuyo valor relajaría un
nivel superior según `_relaxes` — la asignación fallará), además del permiso
del actor sobre ese destinatario (403 si no lo tiene).
> **AC-041-004**: (API+test) preview contra un depto con un valor ya fijado a
> ese nivel → `overwrites: true` con `current_value`; preview de una política
> con `require_mfa` a un usuario → `skipped_org_only: ["require_mfa"]`;
> preview de una policy que relaja la org (p. ej. `web_domain_policy` open
> contra org en allowlist) → `would_relax` con la key; preview no escribe
> nada (sin cambios en `policies` ni en `named_policy_assignments`).

**REQ-041-005** (M) [D-41-01/04/05] (asignación atómica + unassign segura) —
Cuando el admin asigna (`POST /api/named-policies/{id}/assign` con
`level`, `department_id?`, `user_id?`), el sistema DEBE: (a) verificar el
permiso del nivel (D-41-09); (b) omitir las keys `org_only` si el destino no
es org (reportadas en la respuesta); (c) ejecutar `policies.upsert` por cada
key restante EN UNA transacción — si cualquier upsert falla, revertir todo y
responder 422 con el detalle por key; (d) guardar la asignación con
`applied_keys` = mapa efectivamente escrito; (e) auditar
`named_policy.assigned`. Cuando desasigna
(`DELETE /api/named-policies/assignments/{assignment_id}`), el sistema DEBE
desactivar por `policies.deactivate` SOLO las keys cuyo valor actual en el
nivel sigue igual al snapshot, conservar y reportar las modificadas
manualmente o por otra política, y auditar `named_policy.unassigned`.
> **AC-041-005**: (API+test E2E) asignar "desarrollador" a un depto → 201,
> cada key de la definition presente en `policies` a nivel dept (verificable
> por `GET /api/policies` level=dept), `applied_keys` == definition;
> asignación que relaja → 422 con la key conflictiva y CERO filas nuevas en
> `policies` (atomicidad); asignar a usuario una policy con `require_mfa` →
> respuesta con `skipped: ["require_mfa"]` y sin fila user de esa key;
> editar a mano una key asignada y desasignar → la key manual se CONSERVA y
> aparece en `kept_modified` de la respuesta; desasignar limpio → keys del
> snapshot desactivadas (is_active=false) y `effective` del destinatario
> vuelve al valor previo.

**REQ-041-006** (M) [D-41-06] (edición → stale → reapply explícito) — Cuando
una política con asignaciones es editada, el sistema DEBE marcar sus
asignaciones como desactualizadas (`stale`) en las respuestas de la API
(comparando `updated_at` de la política con `applied_at` de la asignación);
y cuando el admin reaplica (`POST /api/named-policies/{id}/reapply`), el
sistema DEBE re-materializar cada asignación de forma atómica por
destinatario (misma semántica que REQ-041-005), respondiendo el resultado
por destinatario (`applied` | `failed` + errores reales) y refrescando el
snapshot de las exitosas.
> **AC-041-006**: (API+test) editar definition → la lista de la política
> muestra la asignación con `stale: true`; reapply con 2 asignaciones donde
> una ahora relaja → respuesta `{results: [{assignment, status: "applied"},
> {assignment, status: "failed", errors: [...]}]}`, la fallada NO cambia sus
> keys, la exitosa refresca `applied_keys`/`applied_at` y deja de ser stale.

### Plantillas y destinatarios

**REQ-041-007** (M) [D-41-08] (plantillas pre-cargadas editables) — Cuando se
aprovisiona un tenant nuevo, el sistema DEBE sembrar las 3 plantillas del
contrato (`solo-lectura-web`, `desarrollador`, `restringido-total`) como
`named_policies` con `source_template='builtin:<slug>'` (idempotente por
slug), y una migración DEBE hacer el backfill para los tenants existentes.
Las plantillas DEBEN ser editables, asignables y borrables como cualquier
política, y la UI DEBE ofrecer "Nueva política desde plantilla" (copia de la
definition).
> **AC-041-007**: (test+DB) tras provisioning, `SELECT count(*) FROM
> named_policies WHERE source_template LIKE 'builtin:%'` == 3 por tenant;
> re-correr el seed no duplica (idempotencia por slug); tras la migración,
> los 2 tenants existentes tienen las 3 plantillas; cada definition de
> plantilla pasa la validación de REQ-041-001 (test que crea una política
> desde cada template).

**REQ-041-008** (M) [D-41-12] (vista de destinatarios) — Cuando el admin
consulta `GET /api/named-policies/recipients`, el sistema DEBE devolver, para
la organización, cada departamento y cada usuario de su alcance, la lista de
políticas nombradas asignadas (id, nombre, badge `stale`), leída de
`named_policy_assignments` — sin re-derivar valores efectivos.
> **AC-041-008**: (API+test) con 1 política asignada a un depto y otra a un
> usuario → recipients lista el depto con la primera y el usuario con la
> segunda; un `dept_admin` de otro depto NO ve asignaciones ajenas (403/datos
> fuera de alcance); tenant B no ve las de tenant A (aislamiento).

### Transversales

**REQ-041-009** (M) [constitution III] (multi-tenant) — Todas las tablas y
queries de políticas nombradas DEBEN llevar `tenant_id` con RLS equivalente a
la del resto de tablas del tenant, y ningún endpoint accede datos sin scope.
> **AC-041-009**: (test) test de aislamiento existente extendido: admin del
> tenant A no lista/crea/asigna/edita políticas del tenant B (404/403), y a
> nivel SQL el RLS de `named_policies`/`named_policy_assignments` filtra por
> `tenant_id` (verificación con `current_user` de otro tenant).

**REQ-041-010** (M) [specs 005/029/036] (engine intacto + backwards
compat) — La capa DEBE dejar intactos `combinator.py` (deny-overrides) y la
semántica de `policies.py` (validación 422, no-relajación REQ-POL-007,
audit por key); los endpoints `GET /api/policies/matrix`,
`GET /api/policies/effective`, PUT/DELETE por nivel y el bundle JWS NO
cambian de contrato; el panel legible de spec 036 y la vista técnica de
spec 033 permanecen funcionales.
> **AC-041-010**: (review+CI) diff sin cambios en `combinator.py`; `policies.py`
> solo importado/reusado, no modificado (salvo extracción read-only si dev lo
> justifica en el plan); suite governance completa verde; spec 036 E2E
> re-ejecutado sin regresiones (panel por key accesible desde la pestaña
> "Ajustes individuales").

**REQ-041-011** (M) [D-41-11] (audit completo) — Cuando ocurre cualquier
operación de política nombrada (create/update/delete/assign/unassign/
reapply), el sistema DEBE emitir el evento `named_policy.*` correspondiente
en el audit log inmutable con payload completo.
> **AC-041-011**: (test) tras el flujo completo del AC-041-005, el audit log
> contiene los 6 tipos de evento con `tenant_id`, actor y `keys[]` en payload.

### UI Policy Builder

**REQ-041-012** (M) [D-41-03/10] (lista + editor con sidebar) — Cuando un
admin navega a `/policies`, la vista por defecto DEBE ser la pestaña
"Políticas": lista de políticas nombradas (tarjeta: nombre, descripción,
badge de plantilla, nº de asignaciones, badge "Desactualizada" si alguna
asignación stale) con "+ Nueva política" (en blanco o desde plantilla) y
buscador por nombre/descripción. Al abrir una política, el editor DEBE
mostrar: nombre + descripción editables y el SIDEBAR de capacidades con los
6 grupos del contrato, cada capacidad con su toggle (y su control de spec
036 al activarse: sub-toggles, select, número, dominios, mensajería), el
resumen de keys compuestas ("12 capacidades activas"), los ítems
`requires_choice` bloqueando el guardado hasta elegir valor, y la sección
"Asignaciones" (destinatarios actuales, desasignar, "Aplicar cambios" si
stale). La pestaña "Ajustes individuales" DEBE conservar el panel legible
de spec 036 intacto; "Vista técnica" solo para `org_admin`.
> **AC-041-012**: (E2E) la lista muestra las 3 plantillas sembradas con badge;
> "+ Nueva política" → editor con los 6 grupos y 47 capacidades del contrato
> (nada hardcodeado: provienen del bloque `builder`); activar "Búsqueda en
> internet" compone `allowed_tools` con los 4 ítems ON visibles en el
> resumen; desactivar "Terminal" deja `allowed_terminal_backends` fuera de
> la definition; guardar sin elegir modelo con "Modelos de IA disponibles"
> ON → error inline, no PUT; navegación por teclado funcional; la pestaña
> "Ajustes individuales" renderiza el panel 036 sin regresión.

**REQ-041-013** (M) [D-41-04] (asignación en la UI con preview) — Cuando el
admin hace clic en "Asignar a…" desde una política, la UI DEBE ofrecer
Organización / Departamento (los suyos) / Usuario (de su alcance), cargar el
preview de REQ-041-004 (diff legible: qué se escribe, qué reemplaza, qué se
omite por org-only, qué fallaría por relajación) y, al confirmar, ejecutar
la asignación real mostrando el resultado (keys aplicadas, omitidas) o el
422 real inline con explicación en palabras (mismo patrón que REQ-036-004).
> **AC-041-013**: (E2E) asignar a un usuario una política con `require_mfa` →
> el preview muestra "Se omite a nivel usuario (solo organización)" y el
> resultado confirma la omisión; intentar asignar una política que relaja →
> el preview lo advierte y al confirmar el 422 real se muestra inline sin
> perder el estado del editor.

**REQ-041-014** (M) [D-41-12] (vista por destinatario en la UI) — Cuando el
admin abre un departamento o un usuario (o la vista de destinatarios del
builder), la UI DEBE mostrar las políticas nombradas asignadas como chips
(nombre + badge stale + desasignar), junto a las frases legibles por key de
spec 036 (que siguen viniendo de `matrix`/`effective`).
> **AC-041-014**: (E2E) usuario con "Practicantes" asignada → su vista
> muestra el chip "Practicantes" y las frases efectivas ("Web search —
> Blocked", etc.) coherentes con la policy materializada.

**REQ-041-015** (M) [spec 009] (estándar UI Pro) — El builder DEBE cumplir el
estándar UI Pro: design system existente (Card/Badge/Dialog/toasts/
skeletons), lucide-react (iconos del contrato con verificación vía script
extendido), dark tokens, responsive, accesible (aria, labels, foco por
teclado), estados reales (loading/error/403/422/409).
> **AC-041-015**: (build+E2E) `npm run build` exit 0 y lint limpio; script de
> iconos extendido al contrato del builder (todos existen en lucide-react o
> caen al fallback del grupo); estados 403 (dept_admin fuera de alcance) y
> 409 (borrar con asignaciones) renderizados legibles.

**REQ-041-016** (M) [constitution I] (nada simulado) — Todo render y toda
escritura del builder DEBE provenir de respuestas reales de la API; prohibidos
datos mock presentados como reales. La evidencia E2E del escenario
"Practicantes" (REQ-041-017) DEBE documentarse en
`docs/evidencia/named-policies/`.
> **AC-041-016**: (review) grep sin literales de políticas/grupos/defaults en
> la página; evidencia con login PKCE+TOTP real documentada.

### E2E Practicantes

**REQ-041-017** (M) [mandato] (E2E "Practicantes" en vivo) — Cuando qa/e2e
ejecuta el escenario, DEBE: (1) crear desde la UI la política "Practicantes"
con: sin internet (`allowed_tools` SIN `web_search` + `web_domain_policy`
allowlist vacía), sin terminal (`allowed_terminal_backends: []`), solo el
modelo aprobado elegido (`allowed_models: [<modelo aprobado del tenant>]`),
`approval_mode: "manual"`; (2) asignarla a un usuario real; (3) verificar en
vivo que el Hermes enrolado de ese usuario cumple EXACTO: búsqueda web
BLOQUEADA, terminal BLOQUEADO, el modelo aprobado incluido PASA por LiteLLM,
y un modelo aprobado NO incluido queda BLOQUEADO.
> **AC-041-017**: (E2E en vivo) evidencia documentada con: capturas/traza del
> builder (política creada y asignada por la UI real), `GET
> /api/policies/effective` del usuario mostrando `allowed_tools` sin
> `web_search`, `allowed_terminal_backends: []` y `allowed_models:
> [<modelo>]`, y prueba en vivo desde el Hermes enrolado (web search
> denegado, terminal denegado, request al modelo aprobado OK por el gateway,
> request al modelo excluido denegado). Nada simulado: usuarios, dispositivo
> y modelos reales del tenant de prueba.

## 5. Amenazas y límites

- **Conflicto entre dos políticas sobre la misma key a un mismo destinatario**:
  no hay prioridades — gana la última escritura (el engine solo tiene un valor
  por key/nivel). El preview muestra `overwrites` y el snapshot de ownership
  protege la unassign. Fuera de alcance: semántica de merge entre políticas.
- **Stale drift**: si el admin nunca reaplica, las asignaciones divergen de la
  definition. Mitigado por el badge "Desactualizada" (visible en lista,
  editor y destinatarios); la verdad de enforcement es siempre lo
  materializado en `policies` — el badge es honesto sobre la diferencia.
- **`allowed_models` dinámico**: los nombres de modelo son por tenant
  (discovery dinámico, spec 032); las plantillas NO fijan modelos
  (`requires_choice`) — el admin elige al editar/asignar.
- **Plantillas borradas por el admin**: no se re-siembran (el slug queda
  libre); el re-seed idempotente solo aplica en provisioning/backfill. Es
  consecuencia de "editables" (D-41-08) y se acepta explícitamente.
- **Rendimiento del preview/assign**: una definition puede llevar ~20 keys →
  ~20 upserts en una transacción; volumen trivial para PostgreSQL local.
- **Migración Alembic (hotspot)**: regla 9 del CONTEXTO-TAREAS — el dev DEBE
  anunciar la migración en un comentario de su card ANTES de crearla y usar
  `python3 db/scripts/migration_guard.py new --slug named_policies`, con
  paridad `db/schema.sql` y rebuild de la imagen `migrate`.

## 6. Trazabilidad

| Req | Padre | Componente |
|---|---|---|
| REQ-041-001/002 | REQ-POL-003/004, spec 009 | `platform/app/governance/named_policies.py` (nuevo), router, migración |
| REQ-041-003 | D-36-01, REQ-036-001 | `platform/app/governance/named_policy_builder.json` + test de cobertura |
| REQ-041-004/005/006 | REQ-POL-007, REQ-GOV-021…023, D-36-03 | ídem (preview/assign/unassign/reapply sobre `policies.upsert`/`deactivate`) |
| REQ-041-007 | spec 004 (provisioning), D-41-08 | `platform/app/tenants/provision.py` + backfill |
| REQ-041-008 | D-36-04, spec 033 | ídem (recipients) |
| REQ-041-009 | constitution III | migración + tests de aislamiento |
| REQ-041-010 | specs 005/029/033/036 | transversal (cero cambios de engine) |
| REQ-041-011 | constitution V | `audit.emit` existente |
| REQ-041-012…016 | spec 009, REQ-036-002…008 | `console/src/app/(console)/policies/` (builder + pestañas) |
| REQ-041-017 | mandato Miller, spec 019 (bundle) | E2E trust-e2e/qa + `docs/evidencia/named-policies/` |

## 7. El contrato del builder (entregable de product-owner)

El archivo **`named-policy-builder.json`** (este directorio) es la norma
vinculante del sidebar y las plantillas. Validado el 2026-10-29 contra el
estado real (script de generación+validación ejecutado contra la DB viva y
el catálogo legible 036):

| Check | Resultado |
|---|---|
| Cobertura de keys no invariables por ítem | **44/44** (0 faltantes, 0 sobrantes) |
| Ítems `list-item` de `allowed_tools` vs `known_items` | **4/4 exactos** (web_search, read_file, write_file, governance_status) |
| Grupos del sidebar | **6** (web 3, system 9, comms 5, models 2, security 17, agent 11 ítems) |
| `org_only` en ítems == `ORG_ONLY_KEYS` | ✅ (`require_mfa`, `allow_self_registration`) |
| Invariables excluidas | ✅ (las 3, piso del proveedor — sección "Protecciones del proveedor" de spec 036) |
| Tipos de `default_value` e ítems vs `value_type` de la DB | ✅ (0 errores) |
| Plantillas con definitions válidas (keys no invariables, tipos correctos) | ✅ 3/3 |
| sha256 de la copia canónica | `207ab6b5b88ffd7576688687efd7904467a1dc2ad7726e7705748c744934f8c9` |

Los tres ejemplos de la visión de Miller quedan así en el builder:

| Frase de Miller | Ítem del sidebar | Efecto en la definition |
|---|---|---|
| "sin internet" | "Búsqueda en internet" OFF (grupo Navegación y web) | `allowed_tools` sin `web_search` (+ `web_domain_policy` allowlist vacía para cerrar navegación) |
| "sin terminal" | "Terminal y ejecución" OFF (grupo Sistema y terminal) | `allowed_terminal_backends: []` |
| "solo modelos aprobados" | "Modelos de IA disponibles" ON + elección (grupo Modelos) | `allowed_models: [<aprobados elegidos>]` (el piso `approved_models_only` es invariable: siempre activo) |

## 8. Convergencia

Convergida el 2026-10-29 (fases implement dev → ui → e2e/qa completas).
Evidencia: `docs/evidencia/named-policies/` (README.md fase UI,
`practicantes-e2e.md` fase E2E en vivo, `practicantes-live.log`,
`converge-qa/`).

| AC | Verificación | Resultado | Evidencia |
|---|---|---|---|
| AC-041-001 | API+test (crear/duplicado/invariable/tipo/template/audit) | PASS | `platform/tests/test_named_policies.py` 16 passed (re-run QA); dev t_de4cec58 |
| AC-041-002 | API+test (editar→stale, 409, cascade, 204) | PASS | ídem |
| AC-041-003 | API+test (sha256 contrato, 44/44, 4/4, org_only, bloque builder) | PASS | ídem + live: builder 6 grupos/47 ítems/3 plantillas en `GET /api/named-policies` (suite UI 24/24) |
| AC-041-004 | API+test (preview overwrites/skipped/would_relax/sin escritura) | PASS | ídem + UI: preview real en `practicantes-03-assign-preview.png` |
| AC-041-005 | API+test E2E (assign atómico, 422, skipped, kept_modified, unassign) | PASS | ídem + suite UI (assign/cascade/unassign verificados en vivo) |
| AC-041-006 | API+test (stale + reapply por destinatario) | PASS | ídem + `converge-qa/builder-06-stale.png` |
| AC-041-007 | test+DB (3 plantillas por tenant, idempotente, backfill) | PASS | migración 0034 aplicada (dev, audit chain ok); lista UI muestra las 3 con badge Template |
| AC-041-008 | API+test (recipients, scope dept_admin, aislamiento) | PASS | `platform/tests/test_named_policies.py`; chips por destinatario en vivo |
| AC-041-009 | test (RLS multi-tenant) | PASS | ídem + `db/tests` 219 passed (dev) |
| AC-041-010 | review+CI (engine intacto, matrix/effective/bundle sin cambios, 036 E2E sin regresión) | PASS | `git diff` VACÍO en `combinator.py`/`policies.py`; governance 63 passed (re-run QA); regresión 036/033 17/17 |
| AC-041-011 | test (6 eventos named_policy.* con payload) | PASS | suite dev + live: `named_policy.created/assigned` con `keys[]` en `audit_log` (`practicantes-live.log` R8) |
| AC-041-012 | E2E (lista+editor+sidebar+composición+requires_choice+teclado) | PASS | suite UI 24/24 (re-run QA en `converge-qa/`) |
| AC-041-013 | E2E (preview omisión org-only + 422 inline) | PASS | ídem (assign a usuario con `require_mfa` → "Skipped — organization-level only"; 422 real inline) |
| AC-041-014 | E2E (chip "Practicantes" + frases efectivas) | PASS | `practicantes-05-recipient-chips.png` (chip + "✗ Web search" + "Set for this user") |
| AC-041-015 | build+E2E (build/lint/iconos/403/409) | PASS | `npm run build` exit 0; lint 0 hallazgos en archivos 041 (preexistentes de otras lanes documentados); iconos OK 49+49 |
| AC-041-016 | review (nada simulado, grep sin literales) | PASS | grep limpio (dev ui); toda la evidencia con login PKCE+TOTP real y API viva |
| AC-041-017 | **E2E en vivo "Practicantes"** | **PASS** | `practicantes-e2e.md` + `practicantes-live.log` **29/29**: política creada/asignada por la UI real (Chromium), `effective` EXACTO (sin web_search, terminal [], allowed_models [glm-5.2], manual, allowlist vacía), Hermes enrolado REAL (dispositivo activo, bundle v29) con web_search y terminal BLOQUEADOS, glm-5.2 PASA por el gateway LiteLLM real (200 + sesión live), glm-5.3 y deepseek-v4.1-flash (aprobados NO incluidos) BLOQUEADOS (403 del gateway + sesión live rechazada), audit `named_policy.assigned` |

**Hallazgo no bloqueante** (card creada para ui): el control de dominios
036 reutilizado por el builder (`readable/policy-control.tsx:341-345`)
rechaza componer una allowlist VACÍA desde una política en blanco, aunque
REQ-041-017 la exige y la plantilla `restringido-total` la usa. El valor es
válido para el backend y se obtiene por la vía plantilla (D-41-08, flujo de
Miller, usado en la evidencia). Detalle y captura en
`docs/evidencia/named-policies/practicantes-e2e.md`.

> **Resuelto** (t_edd8acaf, 2026-10-29): el control de dominios ahora acepta
> `allowlist []` desde una política en blanco — se aplica sin guard y muestra
> una advertencia visual ("No domains listed — this allowlist blocks ALL web
> browsing.") en lugar del error. Regresión cubierta en
> `e2e_named_policies_shots.mjs` (composición desde blanco → definition
> `{allowlist, []}`) y `e2e_041_practicantes_ui.mjs` (check FIX). Evidencia:
> `docs/evidencia/named-policies/t_edd8acaf/` (27/27 + 19/19 + 17/17).
