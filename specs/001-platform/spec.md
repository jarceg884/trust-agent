# Spec 001 — Plataforma Trust Agent: SaaS de gobernanza de IA sobre Hermes Agent

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* (SDD) — lista para *clarify → plan → tasks* |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | product-owner |
| **Idioma** | Español (docs) / inglés (UI copy, código) / USD (montos) |
| **Fuentes** | `DECISIONES.md` (D1–D5), `auditoria/AUDITORIA-AIDLC.md` (C1–C6, patrones 1–7, implicaciones 1–10), mockups OCR (`upload_iaadt_lh.png` Admin Console, `upload_lcw3pa1_.png` Desktop), docs oficiales Hermes (plugins, profiles), constitution v1.0.0 |

> Nota de naming: el nombre comercial provisional es **Trust Agent**. Los mockups muestran "Harper", nombre retirado (DECISIONES.md). En UI copy se usa el branding del tenant (REQ-BRD).

---

## 1. Problema y visión

Las organizaciones adoptan agentes de IA sin control: nadie sabe quién usa qué modelo, cuánto cuesta, con qué credenciales accede a qué sistemas, ni queda rastro auditable. Trust Agent es una plataforma SaaS multi-tenant que **gobierna agentes de IA a nivel organizacional** construida sobre Hermes Agent:

- El **proveedor** aprovisiona clientes (tenants) de forma reproducible e "idiom-proof".
- Cada cliente administra su organización desde un **Admin Console**: departamentos, usuarios, presupuestos jerárquicos, políticas, modelos aprobados, agentes compartidos, dispositivos, conectores, vault de credenciales, audit log y branding.
- El usuario final usa el **desktop white-label**, ligado a su organización, y solo ve lo que las políticas le permiten.
- Los modelos se consumen **solo a través de LiteLLM** hacia Huawei Cloud MaaS (modo central del proveedor) o BYOK del cliente, siempre con presupuesto y auditoría.

**El producto ES la gobernanza** (constitution V): cada capacidad debe tener historia de gobernanza — quién puede usarla, con qué presupuesto, bajo qué política, y auditada. Una feature sin policy/budget/audit está incompleta por definición.

## 2. Actores y roles

| Actor | Descripción | Rol RBAC |
|---|---|---|
| **Operador del proveedor** | Empleado del proveedor SaaS; opera el control plane, aprovisiona tenants, gestiona el catálogo global (modelos, conectores, plantillas de perfil). | `platform_admin` |
| **Admin de cliente** | CIO/CTO/líder de la organización cliente; administra SU tenant completo. | `org_admin` |
| **Admin de departamento** | Líder de departamento; administra usuarios, presupuestos y políticas de SU departamento. | `dept_admin` |
| **Usuario final** | Trabajador del cliente; usa el desktop Trust Agent con agentes Hermes. | `user` |
| **Sistema** | Jobs asíncronos, agregador de spend, enforcement de políticas/corte, notificaciones. | (sin rol; actúa en nombre del tenant) |

### 2.1 Matriz de permisos RBAC (decisión de producto, vinculante)

| Capacidad | platform_admin | org_admin | dept_admin | user |
|---|---|---|---|---|
| Aprovisionar/desactivar tenants | ✔ | — | — | — |
| Catálogo global (modelos, conectores, plantillas) | ✔ | — | — | — |
| Branding del tenant | ✔ (default proveedor) | ✔ | — | — |
| CRUD departamentos | — | ✔ | — | — |
| Presupuesto org | — | ✔ | — | — |
| Presupuesto depto | — | ✔ | ✔ (solo su depto) | — |
| Presupuesto usuario | — | ✔ | ✔ (solo su depto) | — |
| Invitar/editar usuarios | — | ✔ | ✔ (solo su depto) | — |
| Políticas org | — | ✔ | — | — |
| Políticas depto | — | ✔ | ✔ (≤ restrictivas que org) | — |
| Aprobar modelos por depto/usuario | — | ✔ | ✔ (subconjunto de los aprobados por org) | — |
| Aprobar/rechazar/revocar dispositivos | — | ✔ | ✔ (solo su depto) | — |
| Grants de agentes compartidos | — | ✔ (cualquier agente del tenant) | ✔ (agentes de su depto) | ✔ (solo agentes que posee) |
| Ver audit log | — | ✔ (todo el tenant) | ✔ (solo su depto) | ✔ (solo sus propios eventos) |
| Gestionar conectores del tenant | — | ✔ | ✔ (solo su depto) | — (solo conecta los suyos, gated por política) |
| Vault de credenciales | — | ve metadatos, nunca secretos | ve metadatos de su depto, nunca secretos | ✔ (solo sus credenciales) |
| Usar el desktop / agentes | — | ✔ (si también es user) | ✔ (si también es user) | ✔ |

Regla transversal: **ninguna combinación de roles cruza el límite del tenant** (constitution III). `platform_admin` opera el control plane pero no consume modelos ni ve secretos de vault de los clientes.

## 3. User stories por rol

### 3.1 Proveedor (platform_admin)

- **US-PROV-1** — Como proveedor, quiero aprovisionar un cliente nuevo con un solo comando/flujo reproducible (org + admin + branding + presupuesto + políticas default + catálogo de modelos), para que el onboarding no dependa de pasos manuales propensos a error. → REQ-ONB-001…009
- **US-PROV-2** — Como proveedor, quiero que un onboarding fallido revierte todo lo creado, para no dejar tenants a medio construir. → REQ-ONB-003
- **US-PROV-3** — Como proveedor, quiero re-ejecutar el aprovisionamiento con la misma clave de idempotencia sin duplicar nada, para poder reintentar con seguridad. → REQ-ONB-002
- **US-PROV-4** — Como proveedor, quiero gestionar el catálogo global de modelos, conectores y plantillas de perfil por departamento, para que cada tenant herede un catálogo curado y versionado. → REQ-MDL-001, REQ-CON-001, REQ-PRF-001
- **US-PROV-5** — Como proveedor, quiero que el uso de modelos MaaS central se mida con margen configurable por tenant, para facturar el consumo con ganancia. → REQ-MDL-003, REQ-MDL-004
- **US-PROV-6** — Como proveedor, quiero desactivar (suspender) un tenant completo, para cortar el acceso ante impago o incidente. → REQ-TEN-006
- **US-PROV-7** — Como proveedor, quiero un audit log de mis propias acciones de aprovisionamiento, para responder ante el cliente por quién hizo qué. → REQ-AUD-003, REQ-ONB-009

### 3.2 Admin de cliente (org_admin)

- **US-ORG-1** — Como org_admin, quiero ver un overview con presupuesto mensual de la organización (total/usado/restante), presupuestos por departamento (límite/usado/miembros) y el remanente no asignado, para controlar el gasto de un vistazo. → REQ-ORG-006, REQ-BUD-001…005
- **US-ORG-2** — Como org_admin, quiero crear departamentos con presupuesto propio, para delegar gasto sin perder el control del total. → REQ-ORG-001, REQ-BUD-002
- **US-ORG-3** — Como org_admin, quiero invitar usuarios por email y asignarlos a un departamento y rol, para incorporar gente sin fricción. → REQ-ORG-002
- **US-ORG-4** — Como org_admin, quiero que el corte de presupuesto detenga el gasto automáticamente cuando se agote, para nunca exceder lo autorizado. → REQ-BUD-006…008
- **US-ORG-5** — Como org_admin, quiero definir qué modelos de IA puede usar cada departamento y usuario, para prohibir modelos no evaluados. → REQ-MDL-001, REQ-MDL-005, REQ-MDL-006
- **US-ORG-6** — Como org_admin, quiero políticas de organización (SSO+MFA obligatorio, solo modelos aprobados, grants explícitos, credenciales scoped) que ningún departamento pueda relajar, para garantizar el piso de seguridad. → REQ-POL-001…004
- **US-ORG-7** — Como org_admin, quiero aprobar y revocar dispositivos, para que solo equipos autorizados accedan. → REQ-DEV-002…005
- **US-ORG-8** — Como org_admin, quiero un audit log inmutable y filtrable de todo lo que ocurre (quién, qué, cuándo, desde qué dispositivo, con qué modelo, costo), para responder a auditores y al CIO. → REQ-AUD-001…007
- **US-ORG-9** — Como org_admin, quiero personalizar el branding (nombre, logo, colores) del Admin Console y del desktop, para que la plataforma se vea como parte de mi organización. → REQ-BRD-001…005
- **US-ORG-10** — Como org_admin, quiero controlar qué conectores (VMware, email, mensajerías) permite la organización por departamento y usuario, para que el agente no toque sistemas no autorizados. → REQ-CON-002…005
- **US-ORG-11** — Como org_admin, quiero ver el estado del gateway de modelos y el gasto casi en tiempo real, para operar antes de que sea demasiado tarde. → REQ-MDL-008, REQ-BUD-005
- **US-ORG-12** — Como org_admin, quiero aplicar plantillas de perfil Hermes por departamento (TI, cloud ops, finanzas, customer ops), para que cada área arranque con un agente útil desde el día uno. → REQ-PRF-001…003

### 3.3 Admin de departamento (dept_admin)

- **US-DEP-1** — Como dept_admin, quiero administrar SOLO los usuarios de mi departamento, para no interferir con otras áreas. → REQ-ORG-004
- **US-DEP-2** — Como dept_admin, quiero repartir el presupuesto de mi departamento entre usuarios y ver el remanente, para gestionar mi techo de gasto. → REQ-BUD-003, REQ-BUD-004
- **US-DEP-3** — Como dept_admin, quiero políticas de departamento más restrictivas que las de la organización (p. ej. conectores permitidos, timeout de sesión), para ajustar el control a mi área. → REQ-POL-002, REQ-POL-003
- **US-DEP-4** — Como dept_admin, quiero aprobar dispositivos de mi departamento, para agilizar la incorporación sin esperar al org_admin. → REQ-DEV-002
- **US-DEP-5** — Como dept_admin, quiero ver el audit log de mi departamento, para investigar incidentes de mi área. → REQ-AUD-004
- **US-DEP-6** — Como dept_admin, quiero otorgar y revocar grants de agentes compartidos de mi departamento, para controlar quién puede invocar qué. → REQ-AGT-002…004
- **US-DEP-7** — Como dept_admin, quiero personalizar el perfil Hermes de mi departamento (skills dentro de lo permitido), para adaptar el agente a nuestro trabajo. → REQ-PRF-005

### 3.4 Usuario final (user)

- **US-USR-1** — Como usuario, quiero descargar el desktop Trust Agent, iniciar sesión con SSO+MFA y que mi dispositivo quede ligado a mi organización, para empezar a trabajar sin configurar nada. → REQ-DTK-001…003, REQ-DEV-001
- **US-USR-2** — Como usuario, quiero ver mis sesiones, capacidades, agentes compartidos y artefactos en un solo lugar, para trabajar con fluidez. → REQ-DTK-003
- **US-USR-3** — Como usuario, quiero ver qué políticas de mi organización me aplican y el estado del presupuesto de mi departamento, para saber mis límites sin preguntar. → REQ-DTK-005, REQ-POL-006
- **US-USR-4** — Como usuario, quiero conectar mis credenciales (VMware, email, mensajerías) en un vault cifrado, para que mi agente opere sistemas sin exponer secretos. → REQ-VLT-001…003, REQ-CON-002
- **US-USR-5** — Como usuario, quiero compartir un agente mío con compañeros mediante grants explícitos, para colaborar sin abrirlo a toda la org. → REQ-AGT-001…003
- **US-USR-6** — Como usuario, quiero mensajes claros cuando una acción me esté bloqueada por política o presupuesto (con el motivo), para entender qué hacer. → REQ-DTK-007, REQ-BUD-006
- **US-USR-7** — Como usuario, quiero que mi agente use solo los modelos aprobados por mi organización sin que yo tenga que elegir, para cumplir las reglas sin esfuerzo. → REQ-MDL-001, REQ-DTK-004
- **US-USR-8** — Como usuario, quiero que las acciones rápidas de mi desktop correspondan a mi departamento (p. ej. "infrastructure health check" en TI), para arrancar con tareas reales. → REQ-DTK-006, REQ-PRF-001

## 4. Requisitos (formato EARS)

Convenciones:
- **Cuando** `<trigger>` **el sistema** `<respuesta>` — plantilla EARS obligatoria.
- Prioridad MoSCoW: **M**ust (bloqueante), **S**hould, **C**ould.
- Cada requisito termina con su criterio(s) de aceptación **AC-…** verificables con evidencia real (test, comando, respuesta HTTP). "Nada simulado" (constitution I): los AC se verifican contra el sistema corriendo, no contra mocks.
- Trazabilidad: `[fuente]` indica DECISIONES.md (D#), AUDITORIA-AIDLC.md (C#, P#=patrón, I#=implicación) o mockup (MOC-ADMIN / MOC-DTK).

### 4.1 Onboarding de tenant por el proveedor (REQ-ONB)

**REQ-ONB-001** (M) [D1, I3] — Cuando el proveedor ejecuta el flujo de aprovisionamiento con los datos mínimos del cliente (`organization_name`, `admin_email`, `initial_budget_usd`, `model_plan`, `branding` opcional, `department_template` opcional), el sistema crea en una única operación orquestada: el tenant, la configuración de identidad (realm/cliente OIDC propio del tenant), el usuario `org_admin` inicial, el branding (default del proveedor si no se provee), el presupuesto de organización, las políticas default del tenant, el catálogo de modelos aprobados según `model_plan` y el departamento inicial opcional — todo con `tenant_id` propio.
> **AC-ONB-001**: script `provision_tenant` con esos 6 parámetros crea un tenant completo consultable por API (org, admin, budget, policies, model catalog) en < 120 s en el entorno local; evidencia: respuestas HTTP 200 de cada verificación + registro en audit log.

**REQ-ONB-002** (M) [I3] — Cuando el proveedor re-ejecuta el aprovisionamiento con la misma `idempotency_key`, el sistema devuelve el estado del tenant existente sin crear duplicados.
> **AC-ONB-002**: dos ejecuciones con la misma clave producen exactamente 1 tenant, 1 org_admin, 1 presupuesto (verificado por conteo SQL).

**REQ-ONB-003** (M) [P3] — Cuando un paso del aprovisionamiento falla, el sistema revierte los pasos ya ejecutados en orden inverso, registra cada paso (éxito/fallo/rollback) en el audit log y devuelve un error con el paso exacto que falló.
> **AC-ONB-003**: inyección de fallo (p. ej. email admin malformado en paso N) deja 0 recursos huérfanos (conteo SQL de tenants/users/budgets igual al estado inicial) y el audit log contiene los eventos de rollback.

**REQ-ONB-004** (M) [C1, I3] — Cuando el aprovisionamiento termina, el sistema envía al `admin_email` un enlace de activación **one-time** con expiración; el primer login obliga a establecer contraseña y enrolar MFA (TOTP mínimo).
> **AC-ONB-004**: el enlace caduca tras su primer uso o tras la expiración (segundo uso → HTTP 410); no se puede completar el primer login sin enrolar MFA.

**REQ-ONB-005** (M) [MOC-ADMIN "Organization policy"] — Cuando se crea un tenant, el sistema aplica las políticas default: `require_mfa=true`, `approved_models_only=true`, `explicit_shared_agent_grants=true`, `scoped_credentials=true`.
> **AC-ONB-005**: `GET /api/policies?level=org` del tenant nuevo devuelve esas 4 políticas activas.

**REQ-ONB-006** (M) [D4, MOC-ADMIN] — Cuando el proveedor define `initial_budget_usd`, el sistema crea el presupuesto mensual de organización en USD (almacenado en centavos, `INTEGER`).
> **AC-ONB-006**: el presupuesto creado persiste `amount_cents = initial_budget_usd × 100` con `period=monthly` y es visible en el overview.

**REQ-ONB-007** (M) [D3] — Cuando no se provee `branding`, el sistema aplica el branding default del proveedor (Trust Agent: nombre, logo, colores); el tenant puede personalizarlo después (REQ-BRD-001).
> **AC-ONB-007**: `GET /api/branding` del tenant nuevo devuelve el branding default sin error.

**REQ-ONB-008** (M) [D1 "idiom-proof"] — Cuando el proveedor aprovisiona, el sistema NO requiere ninguna intervención manual en servicios (IdP, LiteLLM, DB): todo paso es API o script versionado en el repositorio.
> **AC-ONB-008**: la ejecución del aprovisionamiento en un entorno limpio (DB vacía, servicios levantados) no requiere comandos manuales intermedios; el runbook completo es un único script con exit 0.

**REQ-ONB-009** (M) [I7] — Cuando el proveedor aprovisiona, modifica o suspende un tenant, el sistema registra un evento de audit con la identidad del operador del proveedor, el tenant afectado y el timestamp.
> **AC-ONB-009**: el audit log del tenant contiene el evento `tenant.provisioned` con actor del proveedor; el log global del proveedor contiene la referencia cruzada.

### 4.2 Organización, departamentos, usuarios y roles (REQ-ORG)

**REQ-ORG-001** (M) [MOC-ADMIN "Departments"] — Cuando un `org_admin` crea un departamento (`name`, `budget_monthly_usd` opcional, `dept_admin_email` opcional, `profile_template` opcional), el sistema crea el departamento dentro de su tenant con presupuesto y admin de departamento asignables.
> **AC-ORG-001**: POST crea el departamento visible en la tabla del overview (nombre, límite, usado=0, miembros); otro tenant no lo ve (test de aislamiento).

**REQ-ORG-002** (M) [P2] — Cuando un admin invita un usuario (`email`, `department_id`, `role`), el sistema crea un job asíncrono de provisioning (identidad + virtual key LiteLLM + perfil Hermes) cuyo estado es consultable por job id, y envía la invitación one-time.
> **AC-ORG-002**: `POST /api/users` devuelve `job_id`; `GET /api/jobs/{id}` pasa de `pending` a `succeeded` con el usuario consultable; el job sobrevive un reinicio del API (estado en PostgreSQL, no en memoria) — lección C2.

**REQ-ORG-003** (M) — Cuando se asigna un rol, el sistema acepta únicamente los roles de la matriz §2.1 (`platform_admin`, `org_admin`, `dept_admin`, `user`) y aplica exactamente esos permisos.
> **AC-ORG-003**: test de autorización por capacidad × rol coincide 100% con la matriz §2.1 (tabla de casos generada desde la matriz).

**REQ-ORG-004** (M) [C3] — Cuando un `dept_admin` realiza cualquier operación de administración, el sistema limita su alcance a los usuarios/departamentos de su propio departamento y tenant.
> **AC-ORG-004**: un `dept_admin` del depto A recibe HTTP 404/403 (no filtrable) al intentar listar/editar usuarios del depto B del mismo tenant, y de cualquier depto de otro tenant.

**REQ-ORG-005** (M) — Cuando un admin desactiva a un usuario, el sistema revoca en ≤ 60 s: sus sesiones activas, su virtual key de LiteLLM, sus dispositivos enrolados y sus grants salientes de agentes compartidos quedan suspendidos; sus credenciales del vault quedan bloqueadas.
> **AC-ORG-005**: tras desactivar, un request con el token del usuario recibe 401; su virtual key LiteLLM responde 401 en el siguiente intento; sus dispositivos figuran `revoked`.

**REQ-ORG-006** (M) [MOC-ADMIN "Governance overview"] — Cuando un `org_admin` abre el Overview, el sistema muestra: presupuesto mensual de organización (total, usado, restante), tabla de departamentos (límite mensual, usado, miembros), remanente no asignado, proveedor de IA activo y estado de conexión de la organización.
> **AC-ORG-006**: el endpoint `GET /api/overview` devuelve los campos `org_budget{total,used,remaining}`, `departments[{name,limit,used,members}]`, `unallocated`, `ai_provider`, `org_status` y el Admin Console los renderiza; los números cuadran con el ledger de spend (verificación por SQL).

**REQ-ORG-007** (M) [MOC-ADMIN "SSO + MFA required"] — Cuando un usuario sin MFA activo intenta cualquier operación autenticada, el sistema rechaza la petición hasta completar el enrolamiento MFA.
> **AC-ORG-007**: token válido sin MFA enrolada → 403 con `reason=mfa_required` en todas las rutas excepto las de enrolamiento.

### 4.3 Presupuestos jerárquicos con corte (REQ-BUD)

**REQ-BUD-001** (M) [I9, MOC-ADMIN] — Cuando se crea un tenant, el sistema exige un presupuesto mensual de organización en USD (centavos enteros); sin presupuesto no se puede consumir ningún modelo.
> **AC-BUD-001**: tenant con presupuesto 0 → todo request de modelo se rechaza con `reason=budget_exhausted`.

**REQ-BUD-002** (M) [I9] — Cuando un `org_admin` asigna el presupuesto mensual de un departamento, el sistema valida que la suma de presupuestos de departamentos ≤ presupuesto de organización; si excede, rechaza con HTTP 422 y el detalle del exceso.
> **AC-BUD-002**: asignar deptos por $4,000+$3,500+$1,500+$1,500 con org $10,000 → 422; con $1,000 en vez del último → 200 y el overview muestra `$1,000 remains unallocated` (escenario del mockup).

**REQ-BUD-003** (M) [I9] — Cuando un admin asigna presupuesto mensual a un usuario, el sistema valida que la suma de presupuestos de usuarios del departamento ≤ presupuesto del departamento.
> **AC-BUD-003**: análogo a AC-BUD-002 a nivel depto→usuario.

**REQ-BUD-004** (M) [MOC-ADMIN] — Cuando el overview se calcula, el sistema muestra el remanente no asignado (presupuesto org − suma de deptos) por nivel (org→depto→usuario).
> **AC-BUD-004**: `unallocated` del overview == `org.total − Σ dept.limit` (verificación SQL en test).

**REQ-BUD-005** (M) [P1, I9] — Cuando LiteLLM registra un evento de spend, el sistema lo ingiere en un ledger de uso por (tenant, usuario, departamento, modelo, día) y los totales del overview reflejan el gasto con latencia ≤ 60 s (agregación en tiempo casi real).
> **AC-BUD-005**: tras N requests de prueba vía el gateway, `GET /api/overview` refleja el costo esperado (calculado con el pricing vigente) en ≤ 60 s; el ledger contiene una fila por request con costo > 0.

**REQ-BUD-006** (M) [I9, MOC-DTK "Department budget managed by your organization"] — Cuando el gasto acumulado de un usuario en el período alcanza su presupuesto, el sistema **corta**: rechaza sus requests de modelos con HTTP 429 y `reason=budget_exhausted_user` en ≤ 60 s desde el evento de spend que agotó el presupuesto, y notifica al usuario y a su `dept_admin`.
> **AC-BUD-006**: test e2e — consumir el presupuesto de un usuario de prueba (requests pequeños o presupuesto de $0.01) y verificar: request siguiente → 429 con reason; notificación registrada; el usuario sigue pudiendo usar la UI (solo el consumo de modelos se corta).

**REQ-BUD-007** (M) — Cuando el gasto acumulado de un departamento alcanza su presupuesto, el sistema corta el consumo de modelos de TODOS los usuarios del departamento (429, `reason=budget_exhausted_department`), sin afectar a otros departamentos con presupuesto restante.
> **AC-BUD-007**: depto A agotado → usuarios de A reciben 429; usuario del depto B (con presupuesto) sigue operando con 200.

**REQ-BUD-008** (M) — Cuando el gasto acumulado de la organización alcanza su presupuesto, el sistema corta todo consumo de modelos del tenant (429, `reason=budget_exhausted_organization`) y notifica a los `org_admin`.
> **AC-BUD-008**: análogo, a nivel org; verificado con dos deptos y presupuesto org ajustado al mínimo.

**REQ-BUD-009** (M) — Cuando el gasto cruza el umbral 80 % del presupuesto vigente (usuario/depto/org), el sistema notifica al afectado y a su admin antes del corte.
> **AC-BUD-009**: al cruzar 80 % se emite notificación `budget_threshold_80` auditada; el corte posterior incluye el historial de avisos.

**REQ-BUD-010** (M) — Cuando comienza un nuevo período (diario/semanal/mensual según configuración), el sistema reinicia los contadores de gasto automáticamente sin intervención manual.
> **AC-BUD-010**: con `period=daily` en un tenant de prueba, el contador vuelve a 0 tras el reinicio del período (test con reloj simulado o período de 1 min documentado como excepción de test).

**REQ-BUD-011** (M) [D4] — Cuando un usuario consume modelos en modo BYOK, el sistema registra el gasto en el ledger y lo descuenta de los presupuestos igual que el modo central (la gobernanza aplica a ambos modos), aunque el cobro directo sea entre cliente y su proveedor.
> **AC-BUD-011**: request vía BYOK genera fila de ledger con costo calculado y descuenta presupuesto; el corte funciona igual.

**REQ-BUD-012** (S) — Cuando un admin consulta el histórico, el sistema muestra por período: presupuesto vs. usado por nivel (org/depto/usuario) con desglose por modelo.
> **AC-BUD-012**: `GET /api/budgets/history` devuelve períodos cerrados con ambos valores; cuadran con la suma del ledger.

**REQ-BUD-013** (M) — Cuando un admin reduce un presupuesto por debajo del gasto ya incurrido en el período, el sistema exige confirmación explícita (`force=true`) y registra la decisión en audit log; sin confirmación, rechaza con 422.
> **AC-BUD-013**: reducción sin `force` → 422; con `force` → 200 + evento `budget.forced_below_spend` en audit.

### 4.4 Políticas (REQ-POL)

**REQ-POL-001** (M) [MOC-ADMIN, D2] — Cuando un admin define políticas, el sistema las acepta en exactamente tres niveles: organización, departamento y usuario.
> **AC-POL-001**: la API rechaza cualquier otro nivel con 422; las tres niveles son consultables.

**REQ-POL-002** (M) — Cuando se evalúa una petición, el sistema combina las políticas de los tres niveles con semántica **deny-overrides** (la opción más restrictiva gana; una denegación en cualquier nivel no puede ser relajada por otro nivel).
> **AC-POL-002**: test de matriz — org permite conector X, depto lo prohíbe → usuario NO puede; org prohíbe, depto permite → NO puede; ambas permiten → puede. Los 3 casos pasan.

**REQ-POL-003** (M) — Cuando se define una política, el sistema solo acepta claves del catálogo tipado (schema validado, sin free-form): `require_mfa`, `approved_models_only`, `allowed_models`, `allowed_connectors`, `shared_agent_grants_mode`, `vault_allowed_credential_types`, `session_timeout_minutes`, `artifact_sharing`, `messaging_permissions`, `max_daily_sessions`.
> **AC-POL-003**: política con clave fuera del catálogo → 422 con la lista de claves válidas; el catálogo vive en datos por tenant, no como constantes de código [I10].

**REQ-POL-004** (M) [MOC-ADMIN "Organization policy"] — Cuando cualquier nivel intenta relajar una política marcada `invariable=true` (mínimo del proveedor: `require_mfa`, `approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`), el sistema lo rechaza con 422.
> **AC-POL-004**: intentar `require_mfa=false` a cualquier nivel → 422; el set de invariables está fijado en esta spec.

**REQ-POL-005** (M) — Cuando una política cambia, el sistema la versiona, la registra en audit log y la aplica a las sesiones nuevas de inmediato y a las sesiones existentes en su siguiente petición (latencia máxima de efecto: 60 s).
> **AC-POL-005**: cambiar `allowed_connectors` y verificar en ≤ 60 s que una sesión activa recibe el nuevo efecto en su siguiente request.

**REQ-POL-006** (M) [MOC-DTK "organization policies active"] — Cuando el usuario abre su desktop, el sistema muestra un indicador de "políticas de organización activas" con la lista efectiva de políticas que le aplican (resultado de la combinación de los 3 niveles).
> **AC-POL-006**: `GET /api/me/policies` devuelve la política efectiva del usuario; el desktop la renderiza; coincide con el cálculo deny-overrides del backend (mismo test que AC-POL-002 reutilizado).

**REQ-POL-007** (S) — Cuando un `dept_admin` define políticas de departamento, el sistema solo le permite valores iguales o más restrictivos que la política de organización vigente.
> **AC-POL-007**: dept_admin intenta permitir un conector que la org prohíbe → 422.

### 4.5 Model access — LiteLLM como plano de control (REQ-MDL)

**REQ-MDL-001** (M) [D4, P1, MOC-ADMIN "Approved models only"] — Cuando un usuario o agente invoca un modelo, el sistema solo permite modelos presentes en el catálogo aprobado del tenant y permitidos para ese usuario (política `allowed_models`); cualquier otro modelo se rechaza con 403 `reason=model_not_approved` y se audita.
> **AC-MDL-001**: invocación de modelo aprobado → 200; modelo existente pero no aprobado → 403 + evento `model.access_denied` en audit.

**REQ-MDL-002** (M) [P1] — Cuando se provisiona un usuario, el sistema crea una virtual key de LiteLLM por usuario, restringida a los modelos permitidos y con `max_budget`/`budget_duration` vinculados al presupuesto del usuario; las keys se bloquean/desbloquean según el corte (REQ-BUD-006…008).
> **AC-MDL-002**: la virtual key existe en LiteLLM con los modelos correctos; al agotar presupuesto, LiteLLM rechaza con la key bloqueada; al reiniciar el período, la key se rehabilita.

**REQ-MDL-003** (M) [D4] — Cuando el tenant consume modelos, el sistema soporta dos modos por modelo/tenant: **MaaS central** (keys del proveedor hacia Huawei Cloud MaaS; el uso se mide con margen configurable por tenant para facturación) y **BYOK** (keys del cliente, cifradas en el vault, usadas por LiteLLM en su nombre).
> **AC-MDL-003**: un mismo modelo configurado en modo central factura `costo_base × (1+margen)` en el ledger; en modo BYOK factura `costo_base` y usa la key del vault del tenant (verificado en config de LiteLLM generada).

**REQ-MDL-004** (M) [P1] — Cuando el pricing de un modelo cambia en el proveedor MaaS, el sistema sincroniza el pricing (job `pricing_sync`) y el cálculo de costo de nuevos requests usa el pricing vigente; el histórico no se reescribe.
> **AC-MDL-004**: tras `pricing_sync`, el costo por request del modelo refleja el nuevo precio; filas de ledger previas conservan su costo original.

**REQ-MDL-005** (M) — Cuando un admin aprueba o retira un modelo para depto/usuario, el sistema aplica el cambio en ≤ 60 s (la virtual key refleja la nueva lista).
> **AC-MDL-005**: retirar modelo a un usuario → su siguiente request a ese modelo → 403 en ≤ 60 s.

**REQ-MDL-006** (M) [MOC-ADMIN "Model Access"] — Cuando un admin gestiona el Model Access, el sistema ofrece la lista de modelos disponibles (del catálogo global del proveedor) con estado por tenant (aprobado/pendiente/no aprobado), modo (central/BYOK), pricing vigente y a qué deptos/usuarios está asignado.
> **AC-MDL-006**: la vista Model Access lista ≥ 1 modelo real del gateway con esos campos; aprobar/desaprobar desde la UI se refleja en la API.

**REQ-MDL-007** (M) [C4, I6] — Cuando un cliente registra una key BYOK, el sistema la almacena cifrada (vault, REQ-VLT-001), jamás la devuelve en ninguna respuesta de API posterior ni la escribe en logs; LiteLLM la consume desde el vault/config cifrada.
> **AC-MDL-007**: tras registrar la key BYOK, ningún endpoint la devuelve (búsqueda de la key en todas las respuestas del API y en logs = 0 coincidencias); el request con el modelo BYOK funciona.

**REQ-MDL-008** (S) [MOC-ADMIN "AI provider: Huawei Cloud MaaS"] — Cuando un admin consulta el estado del gateway, el sistema muestra proveedor activo, latencia y disponibilidad del último health-check de LiteLLM/MaaS.
> **AC-MDL-008**: `GET /api/models/status` devuelve estado del gateway con timestamp del último check < 60 s.

### 4.6 Agentes compartidos con grants explícitos (REQ-AGT)

**REQ-AGT-001** (M) [P6] — Cuando un usuario publica un agente suyo como compartido, el sistema lo registra como agente compartido del tenant con dueño, descripción y alcance mínimo (departamento u organización), y por defecto **sin ningún grant**.
> **AC-AGT-001**: al publicar, el agente aparece en la lista de compartidos del dueño con `grants=[]`.

**REQ-AGT-002** (M) [P6, MOC-ADMIN "Explicit shared-agent grants"] — Cuando se otorga acceso a un agente compartido, el sistema solo acepta grants explícitos con permiso granular: `invoke` (usarlo), `view` (ver configuración/descripción), `manage` (re-asignar grants, dueño y dept_admin solamente).
> **AC-AGT-002**: otorgar `invoke` a un usuario permite invocarlo pero NO ver su configuración (403 en el endpoint de configuración).

**REQ-AGT-003** (M) — Cuando un usuario sin grant intenta acceder a un agente compartido, el sistema lo rechaza (404 para no revelar existencia, 403 si el agente es público-conocido dentro del tenant) y el agente no aparece en su lista del desktop.
> **AC-AGT-003**: usuario sin grant no ve el agente en `GET /api/agents/shared` ni puede invocarlo (404); tras el grant aparece y funciona.

**REQ-AGT-004** (M) — Cuando un grant se revoca, el sistema impide nuevas invocaciones de inmediato (siguiente request → 404/403) y lo registra en audit.
> **AC-AGT-004**: revocación → siguiente invocación falla; evento `agent.grant_revoked` en audit.

**REQ-AGT-005** (M) [I6] — Cuando un usuario invoca un agente compartido, el consumo de modelos se factura al presupuesto del **invocador**, nunca al del dueño.
> **AC-AGT-005**: invocación por usuario B de agente de usuario A descuenta el presupuesto de B (ledger con `user_id=B`).

**REQ-AGT-006** (M) [MOC-DTK "Shared agents"] — Cuando un usuario abre la pestaña de agentes compartidos en el desktop, el sistema lista solo los agentes con grant vigente para él, con dueño, departamento y permiso.
> **AC-AGT-006**: la lista del desktop == grants vigentes del usuario en la DB (comparación en test e2e).

### 4.7 Dispositivos enrolados (REQ-DEV)

**REQ-DEV-001** (M) [I5, MOC-DTK "Connected to organization"] — Cuando un usuario inicia sesión por primera vez en el desktop, el sistema genera una identidad de dispositivo única (par de claves/token de dispositivo ligado al hardware cuando esté disponible), la registra en estado `pending` y la asocia al usuario y a la organización por defecto.
> **AC-DEV-001**: tras el primer login existe una fila `devices` con `status=pending`, `user_id`, `tenant_id` y huella del dispositivo; el desktop muestra "Connected to organization" solo tras aprobación (REQ-DEV-002).

**REQ-DEV-002** (M) [I5, MOC-ADMIN "Devices"] — Cuando un admin aprueba un dispositivo `pending`, el sistema pasa el dispositivo a `active` y emite un token de dispositivo firmado; si lo rechaza o revoca, el dispositivo pasa a `revoked` y pierde todo acceso.
> **AC-DEV-002**: flujo completo pending→active→revoked verificado por API; en `revoked`, los requests con ese token → 401.

**REQ-DEV-003** (M) — Cuando un dispositivo no enrolado, rechazado o revocado envía una petición autenticada, el sistema la rechaza con 401 `reason=device_not_authorized` (excepto los endpoints del propio flujo de enrolamiento).
> **AC-DEV-003**: token de usuario válido + dispositivo revocado → 401 en cualquier endpoint de negocio.

**REQ-DEV-004** (M) — Cuando se revoca un dispositivo con sesiones activas, el sistema invalida esas sesiones en ≤ 60 s.
> **AC-DEV-004**: sesión activa en el desktop del dispositivo revocado recibe 401 en su siguiente request dentro de los 60 s.

**REQ-DEV-005** (M) [I9, MOC-ADMIN "Devices"] — Cuando un admin consulta la vista Devices, el sistema lista los dispositivos con: usuario, departamento, estado, fecha de enrolamiento, último acceso y contador de sesiones.
> **AC-DEV-005**: la vista lista los dispositivos creados en los tests con todos los campos poblados.

**REQ-DEV-006** (M) [I9] — Cuando se registra cualquier evento de audit, el sistema incluye el dispositivo de origen cuando aplique (evento autenticado).
> **AC-DEV-006**: ≥ 1 evento de audit de test contiene `device_id` no nulo y correcto.

### 4.8 Conectores a aplicaciones (REQ-CON)

**REQ-CON-001** (M) [I10, MOC-ADMIN "Credentials"] — Cuando el proveedor publica el catálogo global de conectores, el sistema lo ofrece por tenant con metadatos: identificador, nombre, categoría (`infrastructure`, `email`, `messaging`), capacidades, scopes requeridos y estado de disponibilidad. Set inicial obligatorio: **VMware** (vSphere/ESXi), **Email** (IMAP/SMTP, Gmail, Exchange), **WhatsApp**, **Telegram**, **Slack**, **Microsoft Teams**.
> **AC-CON-001**: `GET /api/connectors` lista los 6 conectores iniciales con sus metadatos; el catálogo es dato por tenant (tabla), no constante de código.

**REQ-CON-002** (M) [D2] — Cuando un usuario conecta una instancia de un conector (p. ej. su cuenta de VMware o su buzón), el sistema valida que la política `allowed_connectors` se lo permita, almacena las credenciales en el vault (REQ-VLT) y crea la instancia de conector asociada a (usuario, tenant, conector).
> **AC-CON-002**: usuario con permiso conecta VMware con credenciales de prueba → instancia `active`; usuario sin permiso → 403 `reason=connector_not_allowed`.

**REQ-CON-003** (M) [MOC-DTK "Capabilities"] — Cuando el desktop calcula las capacidades visibles de un usuario, el sistema muestra solo los conectores permitidos por su política efectiva; los no permitidos no aparecen (o aparecen deshabilitados con motivo, decisión de UI: **no aparecen**).
> **AC-CON-003**: usuario con `allowed_connectors=[vmware,email]` ve exactamente esos 2 en capacidades; WhatsApp/Telegram/Slack/Teams no listados.

**REQ-CON-004** (M) [D2 "Mensajerías"] — Cuando un usuario conecta una mensajería (WhatsApp, Telegram, Slack, Teams), el sistema aplica la política `messaging_permissions` que define, por plataforma: permitido/prohibido, y modo de operación del agente (`read_only`, `reply`, `full`) — el gateway Hermes solo recibe la plataforma si está permitida y con el modo configurado.
> **AC-CON-004**: usuario con `messaging_permissions={telegram: reply}` conecta Telegram → el agente puede responder pero no iniciar conversaciones ni operaciones `full` (verificado en la config generada del gateway); WhatsApp prohibido → conexión rechazada.

**REQ-CON-005** (M) — Cuando un admin deshabilita un conector a nivel org o depto, el sistema desconecta las instancias activas afectadas en ≤ 60 s y notifica a los usuarios impactados.
> **AC-CON-005**: deshabilitar email a nivel depto → instancias activas del depto pasan a `disconnected` y los usuarios reciben notificación; el depto vecino no se afecta.

**REQ-CON-006** (M) [I6] — Cuando un usuario registra credenciales de un conector, el sistema exige el scope mínimo declarado en el catálogo (p. ej. VMware: read-only vs. admin) y rechaza credenciales con scopes superiores si la política del tenant pide mínimo.
> **AC-CON-006**: intento de credencial VMware admin en tenant con política `scoped_credentials` y conector declarado read-only → 422 con explicación del scope requerido.

### 4.9 Vault de credenciales (REQ-VLT)

**REQ-VLT-001** (M) [C4, I6, MOC-ADMIN "CSMS secrets + KMS encryption"] — Cuando el sistema almacena una credencial, la cifra en reposo con envelope encryption: cada credencial tiene su DEK; las DEK se cifran con una KEK maestra que NO vive en la base de datos (variable de entorno/KMS local); en la DB solo hay ciphertext.
> **AC-VLT-001**: dump de la tabla de credenciales no contiene ningún secreto en claro (búsqueda de los valores de prueba en el dump = 0); sin la KEK del entorno es imposible descifrar (test de arranque sin KEK falla explícitamente).

**REQ-VLT-002** (M) [C1] — Cuando un usuario registra o rota un secreto, el sistema lo muestra **una única vez** en la respuesta de registro/rotación; ninguna respuesta de API posterior, log ni export lo contiene jamás.
> **AC-VLT-002**: tras el registro, `GET` de la credencial devuelve solo metadatos (tipo, conector, scope, fecha); grep del valor del secreto en logs y respuestas de todos los endpoints = 0 coincidencias.

**REQ-VLT-003** (M) [I6] — Cuando el agente de un usuario usa una credencial, el sistema la inyecta en runtime al conector correspondiente sin que el secreto aparezca en el transcript de la sesión, en el contexto del modelo ni en los artefactos generados.
> **AC-VLT-003**: sesión de prueba que usa una credencial marcada (valor canario): el transcript completo y los artefactos no contienen el canario (verificación automatizada sobre el store de sesiones).

**REQ-VLT-004** (M) [MOC-ADMIN "Scoped credentials"] — Cuando la org define `vault_allowed_credential_types`, el sistema restringe qué tipos de credencial puede registrar cada usuario (p. ej. solo `api_key` y `oauth`, no `password`).
> **AC-VLT-004**: usuario intenta registrar tipo no permitido → 422 con la lista de tipos permitidos para su política efectiva.

**REQ-VLT-005** (M) — Cuando un usuario o admin revoca una credencial, el sistema la marca `revoked`, las conexiones que la usan se cortan en ≤ 60 s y el evento queda auditado.
> **AC-VLT-005**: revocación → la instancia de conector asociada pasa a `disconnected` en ≤ 60 s; evento `vault.credential_revoked` en audit.

**REQ-VLT-006** (M) [I9] — Cuando se accede a una credencial (registro, uso por el agente, rotación, revocación), el sistema registra evento de audit con usuario, credencial, acción, dispositivo y timestamp.
> **AC-VLT-006**: cada operación del test e2e del vault deja su evento en audit con los 5 campos.

**REQ-VLT-007** (M) — Cuando un admin consulta el vault, el sistema muestra solo metadatos (nunca secretos): usuario, conector, tipo, scope, estado, última rotación, último uso.
> **AC-VLT-007**: la vista Credentials del Admin Console renderiza metadatos de las credenciales de test; respuesta HTTP inspeccionada sin valores de secreto.

### 4.10 Audit log inmutable (REQ-AUD)

**REQ-AUD-001** (M) [I7] — Cuando cualquier actor intenta `UPDATE` o `DELETE` sobre `audit_log`, el sistema lo impide a nivel de base de datos (permisos/grants de PostgreSQL + trigger de rechazo), no solo a nivel de aplicación.
> **AC-AUD-001**: `UPDATE audit_log SET ...` con el rol de aplicación → error de permiso de PostgreSQL; el trigger devuelve excepción si algún rol con permiso lo intenta.

**REQ-AUD-002** (M) [I7] — Cuando se inserta un evento de audit, el sistema calcula `hash = sha256(hash_prev || payload)` encadenando cada fila a la anterior (por tenant); un verificador detecta cualquier fila alterada, eliminada o insertada retroactivamente.
> **AC-AUD-002**: comando `verify-audit-chain <tenant>` sobre el log íntegro → `OK`; tras manipular una fila con superusuario de test → el verificador señala la fila exacta.

**REQ-AUD-003** (M) [I9, MOC-ADMIN "Audit Log"] — Cuando ocurre cualquiera de estos hechos, el sistema registra un evento de audit: autenticación (login/logout/fallo), cambios de usuarios/roles, cambios de políticas, cambios de presupuestos, grants de agentes, enrolamiento/aprobación/revocación de dispositivos, conexión/desconexión de conectores, operaciones del vault, aprobación de modelos, consumo de modelos (usuario, modelo, costo, dispositivo), acciones del proveedor sobre el tenant.
> **AC-AUD-003**: matriz de cobertura — por cada tipo de evento listado existe al menos un test que lo produce y un assertion de su presencia en audit con los campos: actor, tenant, acción, target, dispositivo (si aplica), costo (si aplica), timestamp.

**REQ-AUD-004** (M) — Cuando un admin consulta el audit log, el sistema ofrece filtros por usuario, departamento, tipo de evento, rango de fechas y texto libre, con paginación; el alcance respeta la matriz §2.1 (dept_admin solo ve su depto; user solo sus eventos).
> **AC-AUD-004**: la misma query con 3 roles distintos devuelve exactamente los alcances esperados (test de autorización sobre audit).

**REQ-AUD-005** (S) — Cuando un admin exporta el audit log, el sistema genera CSV/JSON del rango filtrado dentro del alcance del rol.
> **AC-AUD-005**: export de un rango de test produce un archivo cuyo conteo de filas == resultados paginados de la misma query.

**REQ-AUD-006** (S) — Cuando el tenant define su retención (mínimo 365 días, invariable), el sistema purga eventos solo al vencimiento de la retención y la purga queda ella misma auditada.
> **AC-AUD-006**: intentar retención < 365 días → 422; job de purga registra `audit.purged` con conteo.

**REQ-AUD-007** (M) — Cuando se ejecuta la verificación de cadena (endpoint admin o CLI), el sistema valida la cadena completa del tenant y reporta `OK` o la primera fila inconsistente con su posición.
> **AC-AUD-007**: verificación sobre tenant recién aprovisionado → OK; verificación tras el test de manipulación (AC-AUD-002) → señala la fila.

### 4.11 Branding white-label (REQ-BRD)

**REQ-BRD-001** (M) [D3, MOC-ADMIN "Branding"] — Cuando un `org_admin` edita el branding, el sistema acepta: nombre de producto, logo (light/dark), colores primario/secundario, tagline y favicon; los valida (formato PNG/SVG, tamaño máximo 2 MB) y los versiona.
> **AC-BRD-001**: subir un logo válido → 200; un PNG de 5 MB → 422; el branding queda consultable por API.

**REQ-BRD-002** (M) [D3] — Cuando el branding cambia, el Admin Console y el desktop lo reflejan sin redeploy ni re-empaquetado (los clientes leen el branding activo del tenant en runtime).
> **AC-BRD-002**: cambiar el nombre de producto → el Admin Console y el desktop lo muestran tras recargar (sin reiniciar servicios).

**REQ-BRD-003** (M) [D3, MOC-DTK] — Cuando un usuario descarga el desktop, el instalador/base aplica el branding del proveedor por defecto y, tras el login, el branding activo del tenant del usuario.
> **AC-BRD-003**: el desktop instalado muestra branding default antes del login y el del tenant tras autenticarse (captura/evidencia en el entorno local).

**REQ-BRD-004** (S) — Cuando el admin edita branding, el sistema ofrece preview antes de publicar; los cambios no publicados no son visibles para usuarios.
> **AC-BRD-004**: guardar como borrador → usuarios siguen viendo el branding anterior; publicar → visible para todos.

**REQ-BRD-005** (C) — Cuando el tenant no define branding, el sistema usa el default del proveedor (Trust Agent) en todas las superficies.
> **AC-BRD-005**: tenant de test sin branding → `GET /api/branding` devuelve el default con `source=provider`.

### 4.12 Desktop Trust Agent enrolado a la org (REQ-DTK)

**REQ-DTK-001** (M) [MOC-DTK "SSO+MFA"] — Cuando un usuario inicia sesión en el desktop, el sistema autentica vía OIDC **authorization code + PKCE** con MFA obligatoria (TOTP mínimo); ROPC queda prohibido para usuarios finales.
> **AC-DTK-001**: el flujo de login del desktop usa PKCE (inspección del flujo: `code_challenge` presente, sin password en el token endpoint); sin MFA → 403 `mfa_required`.

**REQ-DTK-002** (M) — Cuando el login tiene éxito, el sistema valida el JWT con JWKS (firma, `exp`, `aud`, `iss`) y emite la sesión del desktop; sesiones y jobs viven en PostgreSQL.
> **AC-DTK-002**: token con firma inválida/expirado/aud incorrecto → 401; la sesión sobrevive el reinicio del API (lección C2).

**REQ-DTK-003** (M) [MOC-DTK] — Cuando el usuario abre el desktop, el sistema muestra: sus sesiones, capacidades (conectores permitidos), agentes compartidos con grant, artefactos, y acciones rápidas; el dispositivo queda ligado a la organización por defecto (REQ-DEV-001).
> **AC-DTK-003**: las 4 secciones del home renderizan datos reales del tenant de test (no vacías ni hardcodeadas); el dispositivo figura `pending` en el Admin Console.

**REQ-DTK-004** (M) [MOC-DTK "Approved MaaS models"] — Cuando el usuario trabaja con su agente, el sistema restringe modelos, conectores y agentes exactamente a lo que su política efectiva permite; nada fuera de política es visible ni invocable.
> **AC-DTK-004**: usuario de test con 2 modelos y 2 conectores permitidos: el selector de modelos lista exactamente esos 2; invocación directa de un tercero por API → 403.

**REQ-DTK-005** (M) [MOC-DTK "Department budget managed by your organization"] — Cuando el usuario consulta su contexto, el sistema muestra: políticas de organización activas, presupuesto de su departamento (estado, no cifra exacta si la política lo restringe) y que usa modelos MaaS aprobados.
> **AC-DTK-005**: los 3 indicadores del mockup renderizan con datos del tenant de test.

**REQ-DTK-006** (M) [MOC-DTK quick actions] — Cuando el desktop sugiere acciones rápidas, el sistema las toma de la plantilla de perfil del departamento del usuario (p. ej. TI: "infrastructure health check", "monthly operations report").
> **AC-DTK-006**: usuario del depto TI de test ve las acciones de la plantilla TI; usuario de finanzas ve las suyas (diferentes).

**REQ-DTK-007** (M) — Cuando una acción del usuario es bloqueada por política o presupuesto, el sistema muestra el motivo específico (`reason` del backend: `budget_exhausted_user`, `connector_not_allowed`, `model_not_approved`, `device_not_authorized`…), nunca un error genérico.
> **AC-DTK-007**: los 4 reasons listados se provocan en tests e2e y el desktop muestra el mensaje correspondiente en inglés (UI copy).

**REQ-DTK-008** (M) — Cuando el usuario inicia una sesión de agente, el sistema ejecuta el agente con el **perfil Hermes del usuario** (home aislado por usuario: config, skills, memoria y sesiones propias) contra el gateway del tenant.
> **AC-DTK-008**: dos usuarios de test en la misma máquina tienen perfiles Hermes separados (`$HERMES_HOME` distintos, memoria/skills no compartidas — verificación de directorios y contenido).

### 4.13 Perfiles Hermes pre-diseñados por departamento (REQ-PRF)

> Mecanismo verificado en docs oficiales de Hermes: un perfil es un home aislado (config, `.env`, `SOUL.md`, skills, memoria, state.db) y las **profile distributions** permiten publicar un perfil como repositorio git versionado que se instala con `hermes profile install <repo>` y se actualiza con `hermes profile update`, dejando credenciales/memoria/sesiones por máquina. La plataforma usa este mecanismo para las plantillas.

**REQ-PRF-001** (M) [DECISIONES "Perfiles Hermes pre-diseñados"] — Cuando el proveedor publica plantillas de perfil, el sistema ofrece mínimo 4: **IT Operations** (service desk, salud de infraestructura, seguridad, reportes operativos), **Cloud Operations** (provisioning, revisión de costos cloud, gestión de incidentes), **Finance** (reportes financieros, conciliación, análisis de gasto), **Customer Operations** (soporte, CRM, plantillas de respuesta). Cada plantilla declara: skills incluidas, herramientas/conectores sugeridos, acciones rápidas del desktop, políticas base sugeridas y presupuesto mensual sugerido.
> **AC-PRF-001**: el catálogo de plantillas lista las 4 con todos los campos declarados; cada plantilla es instalable (`hermes profile install` desde su repo/origen local) y arranca un agente funcional.

**REQ-PRF-002** (M) — Cuando se crea un departamento con `profile_template`, el sistema asocia la plantilla al departamento; los usuarios nuevos del departamento reciben el perfil correspondiente al provisionarse (job de REQ-ORG-002).
> **AC-PRF-002**: usuario invitado al depto "IT" de test arranca con el perfil IT: skills de la plantilla instaladas y acciones rápidas de IT en el desktop (AC-DTK-006).

**REQ-PRF-003** (M) — Cuando el proveedor actualiza una plantilla (nueva versión), el sistema NO altera perfiles ya provisionados; la actualización requiere acción explícita del admin (por departamento) y queda auditada.
> **AC-PRF-003**: publicar plantilla v2 → perfiles existentes siguen en v1 (verificación de versión); aplicar update al depto → perfiles pasan a v2 + evento auditado.

**REQ-PRF-004** (M) — Cuando un usuario se provisiona, su perfil Hermes queda aislado del de otros usuarios (home, memoria, skills y sesiones propios; nunca dos agentes sobre el mismo home).
> **AC-PRF-004**: idéntico a AC-DTK-008 (mismo test).

**REQ-PRF-005** (M) — Cuando un `dept_admin` personaliza el perfil de su departamento (añadir/quitar skills), el sistema valida cada skill contra el catálogo permitido por la política de organización (`allowed_skills`, default: todas las del catálogo global del proveedor) y versiona el cambio.
> **AC-PRF-005**: añadir skill permitida → 200; skill fuera de catálogo → 422; el cambio queda auditado y versionado.

**REQ-PRF-006** (S) — Cuando la plataforma provisiona perfiles o instala skills, el sistema lo hace **exclusivamente vía mecanismos oficiales de Hermes** (profile create/install, plugins con `plugin.yaml` + `register(ctx)`, `ctx.register_tool/register_hook/register_platform`, config por perfil); prohibido sobrescribir archivos del core.
> **AC-PRF-006**: test de CI que compara los archivos del core de Hermes instalados contra su origen upstream → 0 diferencias tras el provisioning completo (lección C6).

### 4.14 Multi-tenancy y aislamiento (REQ-TEN)

**REQ-TEN-001** (M) [D2, C3, I1] — Cuando se crea cualquier tabla de negocio, el sistema incluye `tenant_id` (FK a `tenants`) en todas ellas y activa Row Level Security de PostgreSQL con políticas que filtran por el tenant de la sesión; la app nunca consulta sin scope de tenant.
> **AC-TEN-001**: inspección de schema: 100 % de tablas de negocio con `tenant_id` y política RLS activa; query sin `tenant_id` en el contexto → 0 filas.

**REQ-TEN-002** (M) [C3] — Cuando cualquier endpoint, job o query accede a datos, el sistema exige scope de tenant (middleware + RLS como defensa en profundidad); no existe ruta de acceso sin tenant.
> **AC-TEN-002**: suite de tests de aislamiento: para cada endpoint de negocio, request del tenant A con IDs del tenant B → 404 (no filtrable); jobs ejecutan solo con tenant explícito.

**REQ-TEN-003** (M) [C3] — Cuando se ejecuta la suite de aislamiento en CI, el sistema verifica que un tenant no puede leer, escribir ni enumerar datos de otro por ninguna vía: API, jobs, RLS bypass (SET LOCAL), y exports.
> **AC-TEN-003**: suite `tests/isolation/` en verde con ≥ 1 caso negativo por dominio (usuarios, presupuestos, políticas, dispositivos, conectores, vault, audit, agentes, modelos).

**REQ-TEN-004** (M) [C3] — Cuando un `org_admin` navega el Admin Console, el sistema garantiza que solo ve datos de su tenant; ningún rol de cliente ve el cross-plane (otros tenants, catálogo global, operadores del proveedor).
> **AC-TEN-004**: sesión de `org_admin` del tenant A: enumeración global de tenants → 403; IDs forjados del tenant B → 404.

**REQ-TEN-005** (M) [I3] — Cuando se aprovisiona un tenant, el sistema le asigna identidad aislada: realm/cliente OIDC propio (o claim `tenant_id` firmado si se comparte realm), de modo que los tokens de un tenant no son válidos en otro.
> **AC-TEN-005**: token del tenant A contra el API con `aud` del tenant B → 401.

**REQ-TEN-006** (M) — Cuando el proveedor suspende un tenant, el sistema bloquea todo acceso del tenant (API, desktop, gateway de modelos) en ≤ 60 s conservando sus datos (sin borrar); la reactivación restaura el acceso.
> **AC-TEN-006**: suspender → todos los tokens del tenant reciben 403 `reason=tenant_suspended` en ≤ 60 s; reactivar → acceso restaurado; datos intactos (conteo SQL antes/después).

### 4.15 Seguridad transversal (REQ-SEC)

**REQ-SEC-001** (M) [C6, I3] — Cuando un usuario final se autentica, el sistema usa authorization code + PKCE; ROPC (`password` grant) está prohibido para usuarios finales y solo se admite, si acaso, para bootstrap interno documentado con client_secret.
> **AC-SEC-001**: intento de token ROPC con las credenciales de un usuario final → error del IdP; el único flujo funcional es code+PKCE.

**REQ-SEC-002** (M) [hallazgo alto "JWT sin verificar"] — Cuando el sistema recibe un JWT, verifica firma contra JWKS del IdP, `exp`, `aud` e `iss`; jamás decodifica sin validar.
> **AC-SEC-002**: 4 tokens maliciosos (firma inválida, expirado, `aud` incorrecto, `iss` incorrecto) → todos 401; test unitario del verificador.

**REQ-SEC-003** (M) [C2] — Cuando el sistema maneja sesiones o jobs, los persiste en PostgreSQL con expiración server-side; nada de estado de sesión o job vive solo en memoria del proceso.
> **AC-SEC-003**: reiniciar el API con sesiones/jobs activos → sesiones siguen válidas y jobs retoman/consultan estado (lección C2).

**REQ-SEC-004** (M) [C1] — Cuando el sistema entrega un secreto efímero (password reset, enlace de activación, token de enrolamiento, secret one-time del vault), lo hace una única vez en la respuesta de generación y nunca en respuestas posteriores ni logs.
> **AC-SEC-004**: grep del valor generado en logs y en respuestas posteriores = 0; segundo canje → 410.

**REQ-SEC-005** (M) [C4] — Cuando el sistema almacena cualquier secreto (credenciales de vault, keys BYOK, secrets de conectores del tenant, semillas TOTP), lo cifra en reposo (REQ-VLT-001 o equivalente); prohibido texto claro en DB, archivos de config o logs.
> **AC-SEC-005**: auditoría automatizada: búsqueda de patrones de secretos conocidos (valores canario) en DB, configs generadas y logs = 0.

**REQ-SEC-006** (M) [hallazgo medio] — Cuando llegan intentos de login fallidos, el sistema aplica rate limiting por cuenta y por IP con lockout progresivo, persistido en DB (no en memoria del proceso).
> **AC-SEC-006**: 5 intentos fallidos → 429 con backoff; el contador sobrevive reinicio del API.

**REQ-SEC-007** (M) [C6] — Cuando el sistema establece cualquier conexión TLS interna o externa, verifica el certificado (`verify=True` siempre); prohibido desactivar la verificación.
> **AC-SEC-007**: grep del código de la plataforma: 0 ocurrencias de `verify=False`; test de conexión con certificado inválido → fallo explícito.

**REQ-SEC-008** (M) [C6, regla 5 CONTEXTO] — Cuando el equipo integra con Hermes, el sistema lo hace exclusivamente vía plugins/config oficiales (profile create/install, `plugin.yaml`, `register(ctx)`, `ctx.register_*`); el CI falla si detecta sobrescritura de archivos del core de Hermes.
> **AC-SEC-008**: idéntico a AC-PRF-006; además, el listado de artefactos de integración (plugins propios) vive bajo el directorio de plugins, nunca bajo rutas del core.

**REQ-SEC-009** (M) [hallazgo alto "nohup"] — Cuando la plataforma despliega servicios en el entorno local, cada servicio corre como proceso propio supervisado con healthcheck (`/healthz`); nada de procesos nohup dentro de otro proceso.
> **AC-SEC-009**: cada servicio (API, workers, LiteLLM, IdP) tiene healthcheck verificable y se reinicia de forma independiente sin tumbar a los demás.

**REQ-SEC-010** (M) [hallazgo medio CSRF] — Cuando el Admin Console establece sesión, el sistema usa cookies `Secure`+`HttpOnly`+`SameSite=Lax` (o superior) y protección CSRF en las mutaciones.
> **AC-SEC-010**: inspección de cookies cumple los 3 flags; POST sin token CSRF desde origen cruzado → 403.

**REQ-SEC-011** (S) [I3 "MFA resistente a phishing"] — Cuando un tenant quiera MFA más fuerte, el sistema soporta WebAuthn/passkeys como segundo factor opcional además de TOTP.
> **AC-SEC-011**: flujo de enrolamiento y verificación WebAuthn funcional en el entorno local (emulador de autenticador aceptable como evidencia).

## 5. Criterios de aceptación — consolidado de verificación

Cada AC-… de la sección 4 es **verificable con evidencia real** (constitution I). La convergencia de esta spec exige:

1. **Cobertura**: todo REQ-XXX tiene ≥ 1 AC y todo AC referencia su REQ (trazabilidad bidireccional; verificación por script de parseo del spec.md).
2. **Ejecución**: los AC se verifican contra el sistema corriendo en el entorno local (tests automatizados, comandos con salida real, respuestas HTTP capturadas). Prohibido marcar un AC como pasado con mocks o datos inventados.
3. **Aislamiento**: la suite de aislamiento multi-tenant (AC-TEN-002/003) en verde es condición de convergencia global.
4. **Escenario mockup**: existe un test e2e "Demo Organization" que reproduce el escenario del mockup Admin Console (org $10,000; IT $4,000/Customer Ops $3,500/Finance $1,500; $1,000 unallocated) y verifica los números del overview contra el ledger.
5. **Escenario desktop**: existe un test e2e del flujo completo del usuario final: descarga/login PKCE+MFA → enrolamiento de dispositivo pendiente → aprobación → sesión de agente con modelo aprobado → corte de presupuesto con motivo claro.

## 6. QA — checks de convergencia (fase implement/converge)

- [ ] Tests unitarios por dominio (presupuestos, políticas deny-overrides, hash chain, envelope encryption).
- [ ] Tests de integración (LiteLLM spend → ledger → corte; IdP OIDC; jobs en DB).
- [ ] Tests E2E por user story (US-PROV-1…7, US-ORG-1…12, US-DEP-1…7, US-USR-1…8 — al menos un test por story).
- [ ] Tests de aislamiento multi-tenant (REQ-TEN-002/003) — bloqueantes.
- [ ] Tests de seguridad (REQ-SEC-001…010) — bloqueantes.
- [ ] Test de integridad del audit log con manipulación real (AC-AUD-002).
- [ ] Test de presupuesto con corte real vía LiteLLM (AC-BUD-006…008).
- [ ] Verificación de no-parcheo del core de Hermes (AC-PRF-006/AC-SEC-008).
- [ ] Escenarios mockup (§5.4 y §5.5).
- [ ] Evidencia empaquetada: salidas de tests, capturas y comandos en `specs/001-platform/evidence/`.

## 7. Fuera de alcance (v1)

- **Facturación/cobro real** (pasarelas de pago, invoices): la plataforma mide uso con margen (REQ-MDL-003) pero no cobra. El cálculo de margen y el ledger SÍ están en alcance.
- **Despliegue productivo / infra cloud** (constitution VII): todo corre local single-node.
- **Apps móviles nativas.**
- **Conectores fuera del set inicial** (REQ-CON-001): el catálogo es extensible por datos, pero v1 entrega los 6.
- **SSO federado del cliente** (Okta/Entra externos como IdP del tenant): v1 usa el IdP de la plataforma; el diseño no lo excluye para v2.
- **Alta disponibilidad / multi-región.**

## 8. Trazabilidad a fuentes

| Dominio | DECISIONES | Auditoría | Mockup |
|---|---|---|---|
| Onboarding (REQ-ONB) | D1, D4 | P3, I3, C1 | — |
| Org/deptos/usuarios (REQ-ORG) | D2 | C2, C3, P2 | MOC-ADMIN (Departments, Users & Roles) |
| Presupuestos (REQ-BUD) | D4 | P1, I9, hallazgo "presupuestos solo por usuario" | MOC-ADMIN (Budgets, overview) |
| Políticas (REQ-POL) | D2 | I10 | MOC-ADMIN (Organization policy), MOC-DTK |
| Model access (REQ-MDL) | D4 | P1, C4, I6, I10 | MOC-ADMIN (Model Access), MOC-DTK |
| Agentes compartidos (REQ-AGT) | — | P6 | MOC-ADMIN (Shared Agents), MOC-DTK |
| Dispositivos (REQ-DEV) | D3 | I5 | MOC-ADMIN (Devices), MOC-DTK |
| Conectores (REQ-CON) | D2 | I6, I10 | MOC-DTK (Capabilities) |
| Vault (REQ-VLT) | — | C1, C4, I6 | MOC-ADMIN (Credentials, vaults) |
| Audit (REQ-AUD) | — | I7, hallazgo "audit plano manipulable" | MOC-ADMIN (Audit Log) |
| Branding (REQ-BRD) | D3 | — | MOC-ADMIN (Branding), MOC-DTK |
| Desktop (REQ-DTK) | D3 | I5, I3 | MOC-DTK (completo) |
| Perfiles por depto (REQ-PRF) | "Perfiles Hermes pre-diseñados" | C6, lección de integración | MOC-DTK (quick actions) |
| Multi-tenancy (REQ-TEN) | D2 | C3, I1, I3 | — |
| Seguridad (REQ-SEC) | D5 | C1, C2, C4, C6, altos y medios | — |

## 9. Decisiones de producto fijadas en esta spec (vinculantes para tareas 002 y 003)

1. **Moneda y precisión**: USD; montos almacenados en **centavos enteros** (`BIGINT`/`INTEGER`), nunca float. Costos por request en el ledger en centavos con redondeo documentado.
2. **Identificadores**: requisitos `REQ-<DOM>-NNN`, criterios `AC-<DOM>-NNN`, stories `US-<ROL>-N` — estables; las tasks 002/003 referencian estos IDs, no los renumeran.
3. **Semántica de políticas**: deny-overrides en 3 niveles (org/depto/usuario) con invariables del proveedor (REQ-POL-004). El catálogo de claves de política es dato por tenant (REQ-POL-003).
4. **Latencias de gobernanza**: toda acción de gobernanza (corte, revocación, cambio de política, desconexión de conector) es efectiva en ≤ 60 s. Es un requisito de producto, no de infra: la arquitectura (tarea 003) debe justificar cómo lo cumple.
5. **Grants de agentes**: modelo own/invoke/view/manage (extensión del patrón P6 de la auditoría); facturación de invocaciones al invocador.
6. **Perfiles por departamento**: mecanismo = profile distributions oficiales de Hermes (repo git versionado por plantilla, `hermes profile install/update`); prohibido generar perfiles parcheando core (C6).
7. **Desktop**: el enrolamiento de dispositivo es de primera clase (pending → active/revoked) y el tenant es el default de ligado (D3).
8. **UI copy en inglés, docs en español, montos USD** (constitution VI). Los `reason` de error (`budget_exhausted_user`, etc.) son contrato de API estable.
9. **Escenario de referencia**: "Demo Organization" del mockup como fixture canónico de tests e2e (§5.4).
10. **IdP**: un realm/cliente OIDC por tenant con claim `tenant_id` firmado; PKCE obligatorio; ROPC solo bootstrap interno si es imprescindible (documentar cuál).

## 10. Abiertas (no bloquean plan/tasks; resolver en clarify si el humano decide)

1. **Nombre comercial definitivo**: "Trust Agent" es provisional (DECISIONES). El branding es dato (REQ-BRD), cambiarlo no toca código.
2. **Margen por defecto** del modo MaaS central: valor inicial por tenant pendiente de decisión comercial (REQ-MDL-003 lo hace configurable).
3. **WebAuthn** (REQ-SEC-011): Should — confirmar si sube a Must para v1. **Estado (spec 013, oct-2026): sigue PENDIENTE DE DECISIÓN HUMANA** — escalado como card bloqueada `needs_input`; TOTP con `acr≥2` (spec 011) es el segundo factor vigente. Sin implementación hasta que el humano decida.
4. **Retención de audit > 365 días** por tenant: rango máximo pendiente (REQ-AUD-006 fija el mínimo). El MÍNIMO ya está implementado (spec 013 D-013-04: `PUT /api/admin/audit/retention` con piso 422 + job de purga `audit_purge_expired` con checkpoint de cadena).
