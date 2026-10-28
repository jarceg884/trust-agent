# Spec 021 — Audit UX: filtros, timeline por actor/sesión, integridad visible y export (feedback Miller #9)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | ui (tarea t_c14d6703, feedback Miller #9) |
| **Padre** | specs/008-audit/spec.md (API de consulta/export/verify — superficie COMPLETA ya entregada), specs/009-admin-console/spec.md (Admin Console, página Audit REQ-AC-019) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Problema (feedback #9)

"Audit log no tiene lógica". El backend de audit está completo y verificado (spec 008:
cadena hash inmutable, consulta filtrable con alcance por rol, export CSV/JSON,
verificación API+CLI), pero la UI del Admin Console es una tabla cruda de eventos:
los filtros existentes no cubren el recurso (`target_type`/`target_id`), no hay forma
de ver "qué hizo un usuario en orden" (timeline), el estado de integridad de la
cadena solo aparece dentro de un diálogo que hay que abrir, y el export no comunica
qué rango se está exportando. Un CIO no puede *analizar* nada desde la pantalla.

## 2. Alcance

**Dentro de alcance** (UI sobre la API existente de la spec 008 — cero cambios de
backend, cero cambios de schema, la inmutabilidad queda intacta):

1. **Filtros completos** en la página Audit: actor, tipo de evento (action), recurso
   (`target_type` + `target_id`), dispositivo, rango de fechas (`since`/`until`) y
   texto libre (`q`) — todos mapean 1:1 a query params que `GET /api/audit/events`
   ya soporta (REQ-AU-001).
2. **Timeline visual por actor/sesión**: vista alternativa a la tabla que ordena los
   eventos del rango filtrado en orden cronológico ascendente, agrupados por actor y
   divididos en sesiones (límites `auth.login` → `auth.logout`), con rail visual,
   nodos por evento, badge de acción categorizado, target, payload resumido, coste
   USD y hash corto.
3. **Estado de integridad siempre visible**: pill en la cabecera de la página con
   verde (`Chain intact`) / rojo (`Chain broken at seq N`) / neutral (sin verificar),
   alimentado por la verificación existente `POST /api/admin/audit/verify`
   (REQ-AU-011: cada verificación emite `audit.verified` — real).
4. **Export CSV/JSON con trazabilidad del rango**: los botones existentes pasan a
   mostrar el total del rango filtrado activo ("Export CSV · 46 events") para que el
   usuario sepa exactamente qué exporta (D-AU-09: mismo filtro y alcance que la
   consulta).
5. **Evidencia real** en `docs/`: flujo real (provisioning → login PKCE+TOTP →
   acciones de gobernanza) filtrado, verificado y exportado desde la UI contra el
   stack vivo (PostgreSQL 17 + Keycloak 26 + control plane :8000 + console :3100).

**Fuera de alcance**:

- Cualquier cambio en `platform/app/audit/` o en el schema (la API de la spec 008
  es suficiente; si faltara algo, sería tarea del perfil dev).
- Facets server-side (endpoint de valores distintos de actor/acción): v1 agrega
  client-side los valores vistos en los últimos 200 eventos del alcance (ver
  D-21-03 y su limitación documentada).
- Timeline infinita/streaming: la vista usa el tope de la API (`limit` máx 200 por
  página); rangos mayores se acotan con filtros de fecha/actor.
- Nueva columna `session_id` en `audit_log`: las sesiones se derivan de los eventos
  `auth.login`/`auth.logout` existentes (D-21-04).

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Requisitos |
|----|----------|------------|
| D-21-01 | **Cero backend**: toda la UX se construye exclusivamente sobre `GET /api/audit/events` (filtros `actor_id`, `action`, `target_type`, `target_id`, `device_id`, `since`, `until`, `q`), `GET /api/audit/events/export` y `POST /api/admin/audit/verify`. La UI jamás escribe en `audit_log`; la única "escritura" indirecta es el `audit.verified` que emite el propio verificador del backend (por diseño, D-AU-05). Inmutabilidad intacta. | constitution IV, spec 008 |
| D-21-02 | **Integridad visible con auto-verificación acotada**: al montar la página, si el principal es `org_admin`, se llama UNA VEZ por sesión de navegador a `POST /api/admin/audit/verify` (guard en `sessionStorage` con timestamp) y el pill muestra el resultado real: verde `Chain intact` / rojo `Chain broken at seq N`. El botón "Verify chain" re-verifica on-demand siempre. El guard existe para no emitir un `audit.verified` por cada render/visita (ruido de auditoría); la verificación manual nunca está limitada. Principal sin `org_admin`: pill neutral "Verification requires the org admin role" (el endpoint exige el rol — spec 008). El resultado NO se cachea más allá de la sesión de navegador; cada nueva sesión re-verifica. | REQ-21-004, D-AU-05 |
| D-21-03 | **Filtros 1:1 con la API, aplicados server-side**: cada control mapea a un query param existente; nada de filtrar client-side (el alcance por rol y el `total` deben venir del control plane). Los desplegables de actor/acción/recurso se pueblan con valores REALES agregados client-side de los últimos 200 eventos del alcance del principal (una llamada extra `limit=200`) + entrada libre de texto — limitación documentada: los facets cubren la actividad reciente, no el histórico completo. "Apply filters" construye la query; "Reset" la vacía; el estado de cada filtro queda visible en los controles. | REQ-21-001, D-AU-11 |
| D-21-04 | **Timeline por actor/sesión derivada de eventos reales**: la vista timeline pide el rango filtrado (mismos filtros que la tabla, `limit=200`), lo ordena ascendente por timestamp y lo agrupa: (a) por actor (`actor_id`+`actor_role`; eventos `system`/`provider` con `actor_id` null agrupan como "System"); (b) dentro de cada actor, en sesiones delimitadas por `auth.login` (apertura) y `auth.logout` (cierre); los eventos previos al primer login del rango caen en "Earlier activity". Limitación documentada: `audit_log` no tiene columna de sesión — los límites son heurísticos sobre la ventana cargada; si el rango filtrado no incluye el `auth.login`, la sesión aparece sin apertura. Visual: rail vertical con línea conectora, nodo por evento, badge de acción con tono por categoría (auth=info, deny/cutoff/revoked/failed/danger, presupuesto/política=warning, verificación=success, resto=neutral), target `type:short-id`, payload resumido (pares clave:valor acotados, jamás secretos — el backend ya los excluye), coste USD y hash corto con el hash completo en `title`. Botón por fila de actor en la tabla: "View activity" → aplica el filtro de actor y cambia a la pestaña Timeline. | REQ-21-003 |
| D-21-05 | **Export trazable**: los botones CSV/JSON exportan el MISMO rango filtrado que la tabla (comportamiento existente, D-AU-09) y la UI muestra el `total` del rango activo junto a cada botón, más un toast de confirmación. El conteo exportado debe ser igual al `total` de la consulta con los mismos filtros (AC-AU-003 ya lo garantiza a nivel API; la UI lo hace visible). | REQ-21-005, D-AU-09 |
| D-21-06 | **Estándar y accesibilidad**: componentes del design system existente (`Card`, `Badge`, `Field`, `Select`, `Dialog`, `Pagination`, toasts), dark theme por tokens, responsive (grid de filtros colapsable), estados de carga (`TableSkeleton`/skeleton de timeline) y error reales (`ErrorState` con retry), 403 sin `audit.view` → empty state explicativo. Pestañas Table/Timeline con `role="tablist"`/`aria-selected`. UI copy en inglés. | spec 009, constitution VI |

## 4. Requisitos y criterios de aceptación

### 4.1 Filtros y búsqueda

**REQ-21-001** (M) [spec 008 REQ-AU-001] — Cuando un principal con `audit.view`
aplica filtros en la página Audit (actor, action, recurso `target_type`+`target_id`,
dispositivo, rango `since`/`until`, combinables entre sí), el sistema consulta
`GET /api/audit/events` con los query params correspondientes y muestra solo el
subconjunto con su `total` real; "Reset" devuelve el log completo del alcance.
> **AC-21-001**: contra el tenant real de evidencia — (a) filtro por actor admin →
> todos los items tienen ese `actor_id` y `total` == conteo de la misma query vía
> API directa; (b) filtro `action=auth.login` → solo logins; (c) filtro de recurso
> `target_type=department` → solo eventos cuyo target es un departamento;
> (d) rango de fechas que excluye el flujo → `total` menor; (e) combinación
> actor+acción → intersección.

**REQ-21-002** (M) [spec 008 REQ-AU-001, D-AU-11] — Cuando el principal escribe
texto libre, el sistema lo envía como `q` y el backend busca sobre
acción/target/actor_role/payload (comportamiento existente).
> **AC-21-002**: buscar un término presente en payloads reales (p. ej. el nombre de
> un departamento creado en el flujo) devuelve exactamente los eventos que lo
> contienen (verificado contra la misma query por API directa).

### 4.2 Timeline

**REQ-21-003** (M) — Cuando el principal abre la pestaña Timeline, el sistema
muestra los eventos del rango filtrado en orden cronológico ascendente, agrupados
por actor y por sesión (apertura `auth.login`, cierre `auth.logout`), con rail
visual, nodo, badge categorizado, target, payload resumido, coste y hash por evento.
> **AC-21-003**: para el actor admin del flujo real de evidencia, la timeline
> muestra sus eventos en orden ascendente (seq creciente hacia el presente), con al
> menos una sesión abierta por `auth.login` y los eventos de gobernanza del flujo
> dentro de esa sesión; "View activity" desde la tabla filtra por ese actor y
> activa la timeline.

### 4.3 Integridad visible

**REQ-21-004** (M) [spec 008 REQ-AU-011, D-AU-05] — Cuando un `org_admin` abre la
página Audit, el sistema muestra un pill de integridad que se resuelve con una
llamada real a `POST /api/admin/audit/verify` (una por sesión de navegador;
re-verificación manual siempre disponible): verde `Chain intact` si `status=ok`,
rojo `Chain broken at seq N` si `broken`. Sin `org_admin`: pill neutral con el
requisito de rol.
> **AC-21-004**: (a) al abrir la página como org_admin del tenant real, el pill
> pasa de "Verifying…" a verde y la respuesta del verify es `{"status":"ok"}`;
> (b) el evento `audit.verified` con `source="api"` aparece en el log tras la
> verificación (la verificación es real y auditada); (c) la verificación manual
> (botón) re-verifica y refresca el log.

### 4.4 Export

**REQ-21-005** (M) [spec 008 REQ-AU-003, D-AU-09] — Cuando el principal exporta
CSV o JSON, el sistema descarga el rango filtrado activo (endpoint existente) y
muestra el `total` del rango junto a los botones.
> **AC-21-005**: export CSV con filtro por actor activo → el archivo descargado
> tiene tantas filas de datos como el `total` del filtro e incluye las columnas
> `hash_prev`/`hash`; export JSON del mismo rango → mismo número de items.

### 4.5 Calidad

**REQ-21-006** (M) — El console compila limpio (`npm run build` sin errores ni
warnings nuevos de TS/ESLint) y la página degrada con estados reales: carga
(skeleton), error (retry), 403 (empty state de permiso), rango vacío (empty state).
> **AC-21-006**: `npm run build` exit 0 en `platform/console/`; evidencia en
> `docs/audit-ux-evidencia.md` con las salidas reales.

## 5. Contrato de superficie

Sin cambios de API. Archivos nuevos/modificados (todos en
`platform/console/src/app/(console)/audit/`):

| Archivo | Contenido |
|---|---|
| `audit-page.tsx` (rework) | Orquestación: pill de integridad, filtros completos, pestañas Table/Timeline, export trazable, paginación |
| `audit-integrity.tsx` (nuevo) | Pill de integridad (auto-verify 1×/sesión org_admin + verify manual) |
| `audit-timeline.tsx` (nuevo) | Vista timeline por actor/sesión |

## 6. Checklist de convergencia

- [ ] AC-21-001…006 verificados con evidencia real contra el stack vivo.
- [ ] `npm run build` limpio.
- [ ] Evidencia del flujo real (filtros + timeline + integridad + export) en
      `docs/audit-ux-evidencia.md`.
- [ ] tasks.md cerrado; desviaciones documentadas.
