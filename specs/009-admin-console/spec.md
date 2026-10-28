# Spec 009 — Admin Console: panel de administración del tenant (Next.js)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | ui (tarea t_5ce74df8) |
| **Padre** | specs/001-platform/spec.md §4 (REQ-ORG, REQ-BUD, REQ-MDL, REQ-AGT, REQ-DEV, REQ-VLT/CON, REQ-AUD, REQ-BRD), mockup `upload_iaadt_lh.png`, specs 003–008 (API del control plane consumida) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

> ## CORRECCIÓN DE MILLER (2026-10-26, tarea t_360a752e) — sección "Hermes Download" AÑADIDA
>
> El **"Hermes download link"** por tenant fue implementado originalmente en el
> Provider Console (spec 014, D-PC-09/REQ-PC-017) pero **NO va ahí**: el link de
> descarga lo genera y envía el **ADMIN DEL CLIENTE** desde SU Admin Console a
> sus propios empleados; el proveedor solo crea tenants. Esta spec absorbe la
> funcionalidad como la sección **"Hermes Download"** (nueva D-AC-11 y
> REQ-AC-033 abajo); la spec 014 revoca su D-PC-09/REQ-PC-017 con nota cruzada.
> El contrato del link estable `/download/{tenant_slug}` y del management
> `GET/PUT /api/download-link` pertenece a la spec 023 (t_526030f3, lane dev).

---

## 1. Alcance

Admin Console del tenant: la superficie web donde un `org_admin` (y, con alcance
reducido, un `dept_admin`) administra su organización — presupuestos, políticas,
modelos, agentes compartidos, dispositivos, credenciales, audit y branding.

**Dentro de alcance**:

- App Next.js (App Router) + TypeScript + Tailwind CSS v4 en
  `/root/trust-agent/platform/console/`, estándar enterprise (tipografía, spacing,
  estados de carga/error/vacío, accesibilidad, responsive).
- **Autenticación real OIDC authorization-code + PKCE S256** contra el realm del
  tenant (Keycloak), sesión `kind="admin"` del control plane (cookie + CSRF,
  REQ-SEC-010) — el mismo baile server-side que el desktop (spec 010, D-DTB-02).
- **Integración REAL** con la API del control plane (specs 003–008): tipos
  TypeScript derivados del código/OpenAPI real del control plane, cliente tipado,
  proxy BFF server-side, cero datos mock presentados como reales (constitution I).
- Páginas: **Overview**, **Organization**, **Departments**, **Users & Roles**,
  **Budgets**, **Model Access**, **Shared Agents**, **Devices**, **Credentials**,
  **Audit Log**, **Branding** — según el mockup `upload_iaadt_lh.png`.
- Estados de carga (skeletons), error (con `reason`/`request_id` del envelope §5.2)
  y vacío reales en cada página; feedback de mutaciones (toasts); confirmaciones
  para acciones destructivas.

**Fuera de alcance**:

- Endpoints NUEVOS del control plane (directorio de usuarios, CRUD de
  departamentos, API de branding, listado de grants por agente): el contrato
  vive en la spec 001 (§4: `POST /api/users`, `GET /api/branding`) pero NINGÚN
  endpoint existe hoy. Esta spec los consume si existen y degrada con estado
  honesto si no (D-AC-06); la implementación del backend es una tarea dev
  derivada con el contrato exacto (ver §5.3). No se simulan (constitution I).
- El desktop del usuario final (spec 010, lane paralela) y el portal del
  proveedor (provisioning CLI/API ya existente).
- Cambios al schema o al núcleo del control plane.

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-AC-01 | **BFF proxy server-side**: el navegador SOLO habla con el origen del console (`/api/proxy/*` route handler que reenvía al control plane). Los tokens (session/refresh) viven en cookies httpOnly del origen del console; jamás cruzan al navegador. El proxy reenvía la sesión como cookie del control plane (`trust_session`) + `X-CSRF-Token` en mutaciones, de modo que el CSRF del control plane (REQ-SEC-010) sigue siendo quien valida — el console no implementa su propia relajación. Sobre 401 con refresh disponible: rota el refresh UNA vez (`POST /api/auth/refresh`), actualiza cookies y reintenta; si falla → 401 al navegador → redirect a login. | constitution IV, spec 003 §auth, patrón spec 010 D-DTB-03 |
| D-AC-02 | **Login OIDC PKCE S256 server-side**, idéntico al desktop (spec 010): página de login pide el org code (slug = realm), el route handler `/auth/login` genera verifier/state (cookies httpOnly efímeras, 10 min) y redirige al authorize del realm; `/auth/callback` canjea el code, minta sesión `kind="admin"` (`POST /api/auth/session`) y extrae el `trust_session` del `Set-Cookie` de la API (el body devuelve `session_token: null` para sesiones admin). Sin MFA verificada, el control plane responde 403 `mfa_required` a toda la superficie de negocio: el console lo muestra como pantalla de puerta MFA dedicada (no como error genérico). **Enmienda (2026-10-24, integración D-MFA-01 cross-lane con spec 010)**: Keycloak 26 no emite claim `amr`; la evidencia MFA del control plane pasó a ser `amr` OR `acr >= TRUST_MFA_MIN_ACR` (spec 003, `app/auth/mfa.py`). El authorize del console pide `acr_values=2` y el callback implementa **step-up MFA**: si el mint devuelve `mfa_verified=false`, revoca la sesión inservible, re-intenta el authorize UNA vez (la condición LoA del realm fuerza el formulario OTP sobre la sesión SSO) y falla cerrado si persiste sin MFA. Verificado en vivo: login real KC26 (TOTP real) → acr="2" → sesión MFA-verificada (smoke_console.py PASS). | spec 001 REQ-SEC-001/003, spec 003 D-MFA-01, spec 010 D-DTB-02 |
| D-AC-03 | **Tipos derivados del control plane real**: `src/lib/types.ts` documenta, para cada tipo, el endpoint y archivo del que deriva; el snapshot `openapi.json` del control plane se commitea como referencia (`scripts/dump_openapi.py`). Sin codegen automático en v1 (las respuestas del control plane son dicts sin response_model); la derivación manual se verifica contra el código fuente en el converge. | constitution I, task body |
| D-AC-04 | **Tema visual del mockup Admin** (`upload_iaadt_lh.png`): tema claro enterprise — fondo `#f8f9fb`, superficies `#ffffff`, bordes sutiles `#e5e8ee`, acento rojo `#c90104`, tipografía Geist. Sidebar "GOVERNANCE" con navegación completa, topbar con organización, notificaciones reales (`/api/me/notifications`), acciones "Invite user" y "Download desktop app" (esta última enlaza al artefacto del desktop cuando exista; v1 muestra el flujo documentado). Accesible: focus visible, roles ARIA en tablas/diálogos, contraste AA, navegación por teclado; responsive ≥360 px con sidebar colapsable. | mockup, task body, constitution VI |
| D-AC-05 | **Autorización por permisos data-driven**: la UI se adapta a los `permissions` de `/api/me` (matriz del catálogo, D-CP-02/I10) — un `dept_admin` ve las páginas de su alcance y el control plane aplica el scoping real (404/403 sin oráculo). La UI nunca "esconde" un error del servidor: un 403 se muestra como estado de permisos, no se silencia. | spec 001 §2.1, constitution III |
| D-AC-06 | **Degradación honesta donde la API no existe**: Users & Roles (`GET /api/users`), creación de departamentos, Branding (`GET/PUT /api/branding`) y listado de grants por agente NO tienen endpoint hoy. El console implementa el cliente tipado contra el contrato de la spec 001 y muestra el estado real devuelto por el control plane (404 → estado "API no disponible en este build" con explicación y referencia a la tarea dev), con la acción deshabilitada y documentada. PROHIBIDO: datos inventados que parezcan reales (constitution I). Mismo criterio que la lane desktop (D-DTB-08). **Enmienda (2026-10-26, t_6d3f5016)**: `GET/PUT /api/branding` y `GET /api/users` YA existen (spec 012 REQ-ORG-110/111, t_f72dc691) — la degradación honesta queda solo como fallback ante 404/405 reales; el resto de contratos pendientes (grants-list, download-link spec 023) mantiene el patrón. | spec 001 §4, constitution I, spec 010 D-DTB-08, spec 012 |
| D-AC-07 | **Branding white-label en runtime**: el shell consulta `GET /api/branding` al arrancar; si existe, aplica nombre/producto/acenta del tenant; si no (404), usa el default del proveedor ("Trust Agent", acento del mockup). Sin redeploy (REQ-BRD-002). El formulario de la página Branding queda visible en modo preview deshabilitado hasta que exista `PUT /api/branding`. **Enmienda (2026-10-26, t_6d3f5016 — fallo QA Pilar 2 §9)**: `PUT /api/branding` existe (spec 012) y el formulario está cableado de verdad: inputs controlados, PUT con las claves canónicas cambiadas vía el proxy BFF, toast de éxito SOLO tras 200 real, errores 403/422 del control plane mostrados tal cual, refetch tras guardar y preview reactivo. El estado "preview deshabilitado" solo aplica ante 404/405 real (fallback D-AC-06). | REQ-BRD-001/002/005, D-AC-06, spec 012 REQ-ORG-111 |
| D-AC-08 | **Montos**: la API trabaja en centavos USD; la UI formatea con `Intl.NumberFormat` USD. Los inputs de presupuesto aceptan USD decimales y los convierten a centavos (enteros) al enviar — jamás flotantes al API. | constitution VI, spec 005 |
| D-AC-09 | **Puerto y redirect**: el redirect URI canónico del console es `http://127.0.0.1:3000/auth/callback` (default de `oidc_redirect_uris` del control plane). Para ejecuciones locales donde el 3000 está ocupado (p. ej. contenedores ajenos), `TRUST_CONSOLE_REDIRECT_URI` lo overridea — el realm debe tener la URI registrada (el aprovisionamiento la toma del env del control plane). | platform/app/config.py:71 |
| D-AC-10 | **Sesiones y estado en el control plane**: el console NO guarda estado de negocio; su único estado cliente es el de los componentes React (filtros, diálogos). Las cookies del console son custodia de tokens (httpOnly) + token CSRF legible para el header. Nada de estado de sesión en memoria de proceso (constitution IV). | constitution IV |
| D-AC-11 | **Hermes Download vive en el Admin Console del cliente** (corrección Miller, t_360a752e): página `/hermes-download` solo para `org_admin` (gate server-side en la página + item `adminOnly` en el sidebar). El link estable es determinista por tenant — `{download_base}/download/{tenant_slug}` — y se construye **server-side** con el `tenant_slug` real de `/api/me` (cero input manual, cero mock). La gestión enable/disable consume el contrato `GET/PUT /api/download-link` de la spec 023 con degradación honesta (D-AC-06): mientras el endpoint no exista, banner explícito "API not deployed yet — read-only status" y el link sigue siendo copiable/enviable. El envío al empleado abre un borrador `mailto:` / copia un mensaje de invitación desde el equipo del admin — el console NO envía email por sí mismo. Estándar UI pro (mandato Miller, t_7cd4f40e): iconos lucide-react y animaciones con propósito (entrada escalonada `ta-rise`, respeta `prefers-reduced-motion`). | corrección Miller t_360a752e, spec 023 (t_526030f3), spec 014 D-PC-09 revocada, constitution I |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ con AC verificable. Trazabilidad: `[001-…]` = spec maestra, `[003…008-…]` = specs del control plane, `[010-…]` = spec desktop.

### 3.1 Autenticación y sesión

**REQ-AC-001** (M) [001-REQ-SEC-001, D-AC-02] — Cuando un usuario abre el console sin sesión válida, el sistema lo redirige a `/login`; cuando introduce un org code válido y completa el login OIDC (PKCE S256 + MFA del realm), el sistema minta una sesión `kind="admin"` y lo lleva al Overview; cuando el realm/org no existe, muestra el error del IdP sin revelar información.
> **AC-AC-001**: `/login` con org code → 302 al `authorize` del realm con `code_challenge_method=S256` y `state`; callback con `state` correcto → sesión mintada (cookies `ta_console_session`/`ta_console_refresh`/`ta_console_csrf` httpOnly presentes — prefijo por app, F2 de qa-console-repro: las cookies no distinguen puertos y console/desktop comparten host) y redirect al Overview; callback con `state` incorrecto → error en `/login` sin sesión.

**REQ-AC-002** (M) [001-REQ-ORG-007, D-AC-02] — Cuando la sesión existe pero sin MFA verificada (403 `mfa_required` del control plane), el sistema muestra una pantalla de puerta MFA dedicada que explica el requisito y ofrece re-autenticarse; el resto de la app no se renderiza.
> **AC-AC-002**: sesión sin claim `amr` mfa → cualquier página muestra la puerta MFA (no un error genérico ni contenido parcial).

**REQ-AC-003** (M) [D-AC-01] — Cuando el navegador llama a la API (lectura o mutación), el sistema la enruta por el proxy BFF del console: sesión reenviada como cookie del control plane, `X-CSRF-Token` en mutaciones (del control plane espera 403 `csrf_required` si falta), y sobre 401 con refresh disponible rota el refresh una vez y reintenta; sin refresh → redirect a login.
> **AC-AC-003**: mutación sin header CSRF → 403 `csrf_required` del control plane visible en la UI; sesión expirada con refresh válido → la request siguiente funciona sin interacción (cookies rotadas); refresh inválido → redirect a `/login`.

**REQ-AC-004** (M) — Cuando un usuario cierra sesión, el sistema revoca la sesión en el control plane (`POST /api/auth/logout`, auditado) y limpia las cookies locales.
> **AC-AC-004**: logout → `auth.logout` en el audit log del tenant y cookies del console borradas.

### 3.2 Páginas (datos reales del control plane)

**REQ-AC-010** (M) [001-REQ-BUD-004, mockup] — **Overview**: presupuesto mensual de la organización (limite/usado/restante con barra de progreso), tabla de presupuestos por departamento (limite, usado, miembros), monto sin asignar, resumen de política de organización, notificaciones de gobernanza recientes y accesos rápidos. Datos: `GET /api/budgets/overview`, `GET /api/policies?level=org`, `GET /api/me/notifications`.
> **AC-AC-010**: con datos reales del tenant, cada cifra del Overview coincide con la respuesta de `/api/budgets/overview` (verificado en smoke); sin presupuesto org → estado vacío con CTA a Budgets.

**REQ-AC-011** (M) [001-REQ-ORG] — **Organization**: identidad de la organización (slug, estado vía `/api/me`), edición del presupuesto org (`PUT /api/budgets/org` con validación de centavos y manejo del 422 force-below-spend con diálogo de confirmación `force`), y CRUD de políticas org (`GET/PUT /api/policies`, `DELETE /api/policies/{level}/{key}`) con tipos de valor según catálogo.
> **AC-AC-011**: editar el presupuesto org a un valor bajo el spend → la UI ofrece el diálogo "force" (422 → confirmar con `force=true`); crear/eliminar política org se refleja en la lista.

**REQ-AC-012** (M) [001-REQ-BUD-002/003] — **Departments**: lista de departamentos con su presupuesto/usado/miembros (de `/api/budgets/overview`), edición del presupuesto por departamento (`PUT /api/budgets/dept/{id}`, con el 422 de suma ≤ org mostrado accionable), y políticas por departamento. "Add department" deshabilitado con explicación hasta que exista la API (D-AC-06).
> **AC-AC-012**: presupuesto de depto que excedería el org → 422 con el mensaje real del control plane mostrado en el formulario; edición válida → la tabla y el Overview se actualizan.

**REQ-AC-013** (M) [001-REQ-ORG-002, D-AC-06] — **Users & Roles**: directorio de usuarios con roles/estado cuando `GET /api/users` exista; hasta entonces, estado honesto de API no disponible (404 real del control plane) con el contrato documentado y "Invite user" deshabilitado con explicación.
> **AC-AC-013**: contra el control plane actual, la página muestra el estado de API pendiente (sin datos inventados); el cliente tipado compila contra el contrato de la spec 001.

**REQ-AC-014** (M) [001-REQ-BUD-001…004, 012] — **Budgets**: vista jerárquica org→depto→(usuario) con estados de corte (`cutoff`), umbrales y edición en cada nivel soportado por la API (`PUT /api/budgets/org|dept/{id}|user/{user_id}`); presupuestos por usuario solo con el directorio disponible (D-AC-06).
> **AC-AC-014**: la jerarquía refleja `/api/budgets/overview` + `/api/budgets/me`; un departamento en corte se distingue visualmente (badge/estado real del API).

**REQ-AC-015** (M) [001-REQ-MDL-001/005/006] — **Model Access**: matriz del catálogo de modelos (`GET /api/models/access`) con precios USD/millón, badges de aprobación por nivel (org/dept/user, modo central/BYOK), aprobar (`PUT /api/models/access`) y revocar (`POST /api/models/access/revoke`) con diálogo de nivel/alcance.
> **AC-AC-015**: aprobar un modelo a nivel org → el badge aparece en la matriz tras refetch; revocar → desaparece; ambos quedan en el audit log.

**REQ-AC-016** (M) [001-REQ-AGT-001…006] — **Shared Agents**: lista de agentes visibles (`GET /api/agents` con columna de acceso own/grant/manage), publicar agente (`POST /api/agents`), y gestión de grants (`POST /api/agents/{id}/grants` con `permission` invoke|view; revocar por `grant_id`). El listado de grants existentes queda como contrato pendiente (D-AC-06); la UI muestra los grants creados en la sesión y ofrece revocación por ID.
> **AC-AC-016**: publicar un agente → aparece en la lista; crear un grant → el API devuelve el `grant_id` y la UI lo muestra con acción de revocación; permiso inválido → 422 real del control plane.

**REQ-AC-017** (M) [001-REQ-DEV-005, spec 007] — **Devices**: tabla administrativa de dispositivos (`GET /api/devices` con filtros status/departamento), detalle por fila (fingerprint, plataforma, usuario, sesiones activas, last_seen) y acciones approve/reject/revoke con confirmación (revocación = invalidación inmediata de sesiones).
> **AC-AC-017**: dispositivo pending → Approve lo pasa a active (badge actualizado); Revoke → confirmación y estado revoked; los filtros de estado aplican server-side.

**REQ-AC-018** (M) [001-REQ-VLT-001…007, REQ-CON] — **Credentials**: vault de credenciales (`GET /api/vault/credentials` — solo metadata), creación (`POST` → el secreto se muestra EXACTAMENTE una vez en un diálogo one-time con aviso y copia), rotación (`POST …/rotate` con el mismo tratamiento one-time), revocación con confirmación, y sección de conectores: catálogo (`GET /api/connectors`), instancias (`GET /api/connectors/instances`), conectar/desconectar.
> **AC-AC-018**: crear credencial → diálogo one-time con el valor devuelto por el API (jamás re-mostrado: un refetch solo trae metadata); revocar → estado `revoked` en la lista; conectar un conector → instancia visible.

**REQ-AC-019** (M) [001-REQ-AUD-004/005/007, spec 008] — **Audit Log**: tabla filtrable (`GET /api/audit/events`: action, actor, target, dispositivo, rango de tiempo, texto libre `q`) con paginación sobre `total`, export CSV/JSON del rango filtrado, y verificación de cadena (`POST /api/admin/audit/verify`, org_admin) con resultado ok/broken + `seq`.
> **AC-AC-019**: los filtros aplican server-side (params al API); paginar no pierde los filtros; export CSV descarga el mismo `total` de filas que la query paginada; Verify chain → resultado real (ok o broken+seq) visible.

**REQ-AC-020** (M) [001-REQ-BRD-001…005, D-AC-07, 012-REQ-ORG-110/111, 028-REQ-BRD-004] — **Branding**: formulario de white-label (product name, tagline, accent color) con lectura vía `GET /api/branding` y escritura REAL vía `PUT /api/branding` (spec 012, t_f72dc691): inputs controlados, PUT con las claves canónicas cambiadas, toast de éxito solo tras 200, errores del control plane visibles, refetch tras guardar y preview reactivo. Ante 404/405 real del endpoint, estado honesto de API pendiente (D-AC-06). El shell aplica el branding activo en runtime (título del documento, nombre en topbar/sidebar). *(Enmienda t_6d3f5016, 2026-10-26: la versión anterior dejaba el formulario en preview deshabilitado "hasta que exista PUT" — el endpoint ya existía y el botón simulaba un guardado sin request; fallo QA Pilar 2 §9.)* *(Enmienda 2, t_6d3f5016 re-verificación, 2026-10-26: el contrato de escritura es el flujo draft/publish versionado de la spec 028 (t_d2b65bcf, REQ-BRD-004) — `PUT /api/branding` edita el **draft** del servidor (respuesta `{draft, published}`), `POST /api/branding/publish` publica atómico como versión N+1, y los cambios NO son visibles para usuarios hasta publicar. La página además sube logos light/dark + favicon al draft, muestra historial de versiones con rollback y badges draft/published.)*
> **AC-AC-020** *(amendado al contrato draft→publish de la spec 028)*: con sesión org_admin, editar product name + accent color y **Save draft** → `PUT /api/branding` real (solo claves canónicas cambiadas; audit `branding.draft.updated`), toast de éxito SOLO tras el 200 real; **Publish** → `POST /api/branding/publish` (audit `branding.published` con versión N+1), persistencia en `tenants.branding_draft` (draft) y `tenants.branding` (publicado, source: tenant) + fila append-only en `branding_versions`, y la UI refleja los valores publicados tras reload (los cambios sin publicar NO se reflejan — REQ-BRD-004); color inválido → 422 real mostrado; sin permiso → 403 real mostrado; sin endpoint (404/405) → banner de dependencia + preview deshabilitado, sin simular guardado.

**REQ-AC-033** (M) [corrección Miller t_360a752e, D-AC-11, spec 023] — **Hermes Download** (solo `org_admin`): cuando un admin abre `/hermes-download`, el sistema muestra el link estable del tenant (`{download_base}/download/{tenant_slug}`, construido server-side con el slug real de `/api/me`), permite copiarlo y generar un mensaje de invitación / borrador de email para un empleado, y ofrece enable/disable del link contra `GET/PUT /api/download-link` (spec 023); mientras ese endpoint no exista en el control plane, muestra el estado real (404) como banner de contrato pendiente sin bloquear la copia del link. Un usuario sin rol `org_admin` no ve el item en el sidebar y la página muestra el estado de permisos.
> **AC-AC-033**: (1) con sesión de `org_admin`, la página muestra el link con el `tenant_slug` real y "Copy link" lo deja en el clipboard (verificado en navegador + OCR del screenshot); (2) sin rol admin, el item no aparece en el sidebar y la página muestra "Organization admins only"; (3) contra el control plane actual (sin endpoint spec 023), la tarjeta de disponibilidad muestra el banner honesto "Link management API not deployed yet" y NINGÚN toggle simulado; (4) el Provider Console ya NO muestra ningún bloque de download link (nota cruzada spec 014 AC-PC-017 revocado).

### 3.3 Calidad transversal

**REQ-AC-030** (M) [constitution I/VI, task body] — Cuando se ejecuta `npm run build` en `platform/console/`, el build termina limpio (exit 0, sin errores de tipos ni de ESLint bloqueantes); cuando la API está caída, cada página muestra el estado de error real (503 `unreachable` con reintento), nunca datos falsos.
> **AC-AC-030**: `npm run build` exit 0 con evidencia en el converge; con el control plane caído, el Overview muestra el estado de error con botón Retry (verificado en smoke).

**REQ-AC-031** (M) [task body] — Cuando se navega con teclado o lector de pantalla, el sistema es operable: focus visible en todos los interactivos, diálogos con rol `dialog` + focus trap + Escape, tablas con `scope` en headers, estados comunicados con `aria-live`.
> **AC-AC-031**: verificación manual documentada en el converge (checklist de accesibilidad por página).

**REQ-AC-032** (M) [task body] — Cuando el viewport es <1024 px, el sidebar se colapsa a un menú móvil operable y las tablas hacen scroll horizontal sin romper el layout.
> **AC-AC-032**: verificación manual a 375 px documentada en el converge.

## 4. Contrato de superficie

### 4.1 Endpoints del control plane consumidos (existentes, specs 003–008)

| Método | Ruta | Páginas |
|---|---|---|
| POST | `/api/auth/session` (kind=admin), `/api/auth/refresh`, `/api/auth/logout` | auth |
| GET | `/api/me`, `/api/me/notifications`, `/api/me/policies`, `/api/me/budget` | shell, Overview, Budgets |
| GET/PUT | `/api/budgets/overview`, `/api/budgets/org`, `/api/budgets/dept/{id}`, `/api/budgets/user/{id}`, `/api/budgets/me` | Overview, Budgets, Departments |
| GET/PUT/DELETE | `/api/policies` (+`?level=org|dept|user`) | Organization, Departments, Users |
| GET/PUT/POST | `/api/models/access`, `/api/models/access/revoke`, `/api/models` | Model Access |
| GET/POST/DELETE | `/api/agents`, `/api/agents/{id}/grants[/{grant_id}]` | Shared Agents |
| GET/POST | `/api/devices`, `/api/devices/{id}/approve|reject|revoke` | Devices |
| GET/POST | `/api/vault/credentials`, `…/{id}/rotate|revoke|use` | Credentials |
| GET/POST/DELETE | `/api/connectors`, `/api/connectors/available`, `/api/connectors/instances[/{id}]` | Credentials |
| GET/POST | `/api/audit/events`, `/api/audit/events/export`, `/api/admin/audit/verify` | Audit Log |

### 4.2 Contratos pendientes (consumidos con degradación honesta, D-AC-06)

| Método | Ruta | Estado | Fuente del contrato |
|---|---|---|---|
| GET | `/api/users` | No implementado | spec 001 AC-ORG-002 (directorio; `POST /api/users` invite → job) |
| POST | `/api/users` | No implementado | spec 001 AC-ORG-002 |
| GET/PUT | `/api/branding` | **Implementado** (spec 012; PUT edita draft desde spec 028) | spec 001 AC-ONB-007 / REQ-BRD-001/005 + 028 REQ-BRD-004 (draft/publish versionado: `GET /api/branding/draft`, `POST /api/branding/publish`, `GET /api/branding/versions`, `POST /api/branding/rollback`, `POST /api/branding/assets`) |
| GET | `/api/agents/{id}/grants` | No implementado | extensión natural spec 005 (REQ-AGT-004) |
| POST | `/api/departments` | No implementado | extensión natural spec 001 REQ-ORG |
| GET/PUT | `/api/download-link` | No implementado (spec 023, t_526030f3) | spec 023 §management: `GET → {url, enabled, updated_at}`, `PUT {enabled}` (org_admin, auditado); consumido por REQ-AC-033 con degradación honesta |

### 4.3 Rutas del console (Next.js App Router)

```
/login                  org code → PKCE
/auth/login             route handler: inicia el baile (302 al realm)
/auth/callback          route handler: canje + mint + cookies
/auth/logout            route handler: logout + limpieza
/api/proxy/[...path]    BFF proxy (D-AC-01) — TODO el tráfico de datos
/(console)/overview|organization|departments|users|budgets|
  models|agents|devices|credentials|audit|branding|hermes-download
                                          ^ REQ-AC-033, solo org_admin (D-AC-11)
```

## 5. Checklist de convergencia

- [x] AC-AC-001…004 (auth/session/proxy/logout) verificados con evidencia real.
      Evidencia (2026-10-24): `scripts/smoke_console.py` — SMOKE PASS 21/21
      contra Keycloak 26 + control plane + PostgreSQL reales: /login 200,
      /auth/login → authorize con PKCE S256 + redirect :3100 registrada,
      login real (one-time password → UPDATE_PASSWORD → enrolamiento TOTP
      real RFC 6238), callback mint kind=admin (sesión extraída del
      Set-Cookie), cookies ta_console_session/ta_console_refresh/ta_console_csrf, MFA verificada
      (acr="2" vía D-MFA-01), logout → redirect /login + cookies limpiadas.
- [x] AC-AC-010…020 (páginas) verificados contra el control plane real.
      Evidencia: `scripts/smoke_authorized.py` — SMOKE PASS 32/32 (sesión
      admin real en PostgreSQL, whoami/budgets/policies/models/devices/
      vault/agents/audit reales por el proxy, CSRF negativo 403
      csrf_required + positivo 200, mutación reflejada, degradación honesta
      /users y /branding → 404 real, 11 páginas HTML 200, logout). Además
      smoke_console.py verifica los mismos datos con la sesión del login
      REAL de Keycloak.
- [x] AC-AC-030: `npm run build` limpio (exit 0, 18 rutas) — evidencia:
      build final 2026-10-24 "Compiled successfully / Generating static
      pages (18/18)"; `npm run lint` → 0 errors 0 warnings. Con el control
      plane caído el proxy responde 503 `unreachable` (envelope real,
      verificado durante el smoke cuando el :8000 ajeno estaba caído).
- [x] AC-AC-031/032: accesibilidad/responsive implementados y verificados a
      nivel código: focus visible global (`:focus-visible` en globals.css),
      diálogos `role="dialog"` + `aria-modal` + Escape + focus inicial,
      tablas con `scope="col"` en headers, `aria-live="polite"` en toasts y
      errores, labels `htmlFor` en todos los campos, sidebar drawer <1024 px
      con backdrop + Escape, tablas `overflow-x-auto` con `min-w`. (La
      verificación con lector de pantalla queda para el QA final t_bf72a163.)
- [x] AC-AC-033 (Hermes Download, corrección Miller t_360a752e): implementado
      y verificado con evidencia real (2026-10-26, artefactos + OCR en
      `docs/evidencia/hermes-download-admin-console/`): (1) admin del cliente
      (tenant real `hermes-dl`, login PKCE S256 + TOTP acr=2) ve el link
      estable con su `tenant_slug` real; click real en "Copy link" → el
      botón pasa a "Copied" y el clipboard del navegador contiene
      exactamente `http://127.0.0.1:8000/download/hermes-dl` (verificado con
      `navigator.clipboard.readText()` tras permiso CDP); "Copy invite
      message" deja el mensaje de invitación con el link en el clipboard
      (01/02 + OCR); (2) sesión real de un usuario rol `user`
      (`invited-probe@final-e2e.test`, primer login con UPDATE_PASSWORD +
      enrolamiento TOTP): el item NO aparece en el sidebar ni el botón
      "Download desktop app" del topbar, y la página muestra "Organization
      admins only" sin exponer el link (04 + OCR); (3) sin endpoint spec 023
      (GET /api/download-link → 404 real contra el control plane vivo) la
      tarjeta de disponibilidad muestra el banner honesto "Link management
      API not deployed yet" con NINGÚN toggle simulado (01); (4) el
      Provider Console ya no muestra el bloque (03 + OCR).
- [x] Cero datos mock presentados como reales: grep de
      fixtures/faker/msw/mock-data en src/ = 0; las únicas constantes de
      datos son el default del proveedor para branding (REQ-BRD-005,
      marcado `source: provider`) y los labels de UI.
- [x] Tipos `types.ts` trazados al código fuente del control plane
      (spot-check): WhoAmI ← auth/router.py whoami; BudgetOverview ←
      governance/budgets.py overview; AccessOverview ← models/access.py
      access_overview; DeviceRow ← devices/service.py _DEVICE_SELECT;
      CredentialMeta ← vault/service.py _meta; AuditEvent ← spec 008
      REQ-AU-001; GovernanceNotification ← notifications.py list_for_user.
- [x] Tareas dev creadas para los contratos pendientes §4.2:
      t_f72dc691 (users/branding/grants/departments) y t_59afbe56 (bug amr
      KC26 — fix D-MFA-01 ya en el árbol, pendiente SDD formal + suite).

### Desviaciones documentadas

1. **Step-up MFA (D-AC-02 enmendada)**: integración cross-lane con la spec
   010 (D-MFA-01) — el control plane acepta `acr>=2` como evidencia MFA y el
   console hace step-up con `acr_values`. Razón: KC26 no emite `amr`
   (hallazgo real verificado); sin esto, NINGÚN login real alcanzaba la
   superficie de negocio. Verificado en vivo por smoke_console.py.
2. **Puerto 3100 en lugar del 3000 canónico** (D-AC-09): el 3000 del host
   está ocupado por contenedores ajenos (sso-host). El redirect URI se
   registra por env al aprovisionar.
3. **`smoke_authorized.py` usa el IdP de test del plataforma** para el claim
   MFA del tenant demo-org (mismo patrón que la suite e2e, pitfall #9 del
   skill keycloak-admin-oidc): su issuer quedó apuntando a un IdP de test
   histórico (:8908). El camino con Keycloak REAL está cubierto por
   smoke_console.py contra el tenant console-smoke2.
