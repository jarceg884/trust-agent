# Spec 003 — Control plane core: API, auth OIDC, multi-tenant y RBAC

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_028016e6) |
| **Padre** | specs/001-platform/spec.md, docs/arquitectura.md §3.1/§5 (contratos NORMATIVOS), specs/002-data-layer/spec.md (schema físico) |
| **Idioma** | Español (docs) / inglés (código, UI copy) / USD centavos (montos) |

---

## 1. Alcance

Esta spec define el **núcleo del control plane**: el proyecto Python `platform/` con FastAPI + SQLAlchemy 2 sobre el schema de la spec 002, y la maquinaria transversal que consumen todas las specs de dominio (004–008).

**Dentro de alcance**:

- Proyecto `platform/` (pyproject, estructura de módulos vinculante de arq. §3.1) y arranque local (`uvicorn`, `/healthz`).
- Auth OIDC: verificación de JWT vía JWKS (firma RS256/ES256, `exp`, `iat`, `aud`, `iss`, `sub`, leeway ≤ 60 s), mintado/rotación/revocación de **sesiones en PostgreSQL** (nada en memoria), cookie de consola `Secure`+`HttpOnly`+`SameSite=Lax` + CSRF, Bearer para desktop, `MfaGate` (evidencia dual `amr`/`acr`, D-MFA-01 spec 011).
- **Middleware multi-tenant**: `tenant_id` en cada query vía `set_config('app.tenant_id', ..., true)` por transacción como rol `trust_app`; tenant suspendido → 403 `tenant_suspended`.
- **RBAC**: org_admin / dept_admin / user, permisos data-driven desde `catalog.role_permissions` (matriz §2.1 de spec 001), scope de dept_admin limitado a su departamento, cross-tenant → 404.
- **Jobs en PostgreSQL**: enqueue idempotente, claim `FOR UPDATE SKIP LOCKED`, worker `python -m app.jobs.worker`, estado sobrevive reinicios.
- **Servicios transversales**: emisor de eventos de audit (hash chain del schema), combinador de políticas **deny-overrides**, primitiva de envelope encryption del vault, device token firmado + `DeviceGate`, lecturas de catálogos (modelos/conectores).
- Envelope de errores con `reason` estables y `request_id` (arq. §5.2).

**Fuera de alcance** (los implementan las specs hijas sobre esta base):

- Aprovisionamiento de tenants, realms Keycloak, rollback orquestado (→ spec 004-tenant-onboarding, t_8b113637).
- Endpoints de presupuestos, model access, agentes compartidos, pricing sync (→ spec 005-governance, t_3b4fb364).
- API del vault, instancias de conectores, mensajerías (→ spec 006-vault-connectors, t_4b540638).
- Flujo de enrolamiento/aprobación de dispositivos (→ spec 007-devices, t_256e5e87); esta spec entrega solo el device token y el gate.
- API de consulta/export del audit log y CLI verificador (→ spec 008-audit, t_51562aa7); esta spec entrega el emisor y el endpoint de verificación de cadena.
- Rate limiting de login (REQ-SEC-006): resuelto en la evolución de la capa de datos como `ops.login_throttle` (D-DB-14 de spec 002) — tabla cross-tenant en el plano `ops`, sin RLS, aplicada en `POST /api/auth/session`.
- Perfiles Hermes / plantillas (REQ-PRF): sin spec asignada aún; el paquete `app/profiles/` queda reservado por la estructura vinculante.

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-CP-01 | **Estructura de módulos** de arq. §3.1 exacta: `app/{main,config,db}.py` + paquetes `auth, tenants, governance, models, devices, connectors, vault, audit, profiles, jobs`. Los paquetes de dominio contienen SOLO las primitivas transversales; sus routers completos llegan con las specs 004–008. | arq. §3.1 (vinculante) |
| D-CP-02 | **Una transacción de negocio por request**, abierta tras resolver el principal, con `SELECT set_config('app.tenant_id', :tid, true)` como primera sentencia (== `SET LOCAL`, muere con la transacción; jamás `SET` de sesión — pitfall de pools). La app conecta **siempre** como `trust_app` (sin `BYPASSRLS`). | D-DB-02, arq. §3.1 |
| D-CP-03 | **El tenant sale de credenciales verificadas**: del claim firmado `tenant_id` (contrato §5.1) o de la fila `sessions` (buscada por hint del token opaco dentro del scope del hint — un hint falsificado no encuentra la fila). Nunca de un header forjable. | arq. §5.1, ADR-002 |
| D-CP-04 | **Token de sesión opaco** `sts1.<tenant_id>.<session_id>.<secret>`; en DB se guarda `sha256(secret)` en `sessions.jti` (UNIQUE). Refresh token `rt1.<tenant_id>.<session_id>.<secret>` con `sha256` en `refresh_token_hash`; rotación one-time con detección de reuso (reuso → sesión revocada). CSRF token para kind=admin con `sha256` en `csrf_token_hash`. Nada derivable del username (C2). | ADR-007, REQ-SEC-003/004/010 |
| D-CP-05 | **Verificación JWT**: `PyJWKClient` contra `{iss}/protocol/openid-connect/certs` (convención Keycloak, confirmada por discovery), `algorithms=["RS256","ES256"]` (HS256 prohibido), `audience=settings.oidc_audience`, `iss` validado contra `settings.idp_base_url` + coincidencia con `tenants.oidc_issuer_url`, `options={"require": ["exp","iat","aud","iss","sub","tenant_id"]}`, leeway 60 s. Fallo → 401 `invalid_token` sin oráculo de claim. | arq. §5.1, REQ-SEC-002, skill keycloak-admin-oidc |
| D-CP-06 | **El API nunca maneja passwords de usuarios ni token endpoint con `grant_type=password`**. El desktop/console ejecuta authorization-code + PKCE S256 directamente contra el realm del tenant; el API solo recibe el `id_token` resultante y lo verifica (mintado de sesión). ROPC queda para `admin-cli` interno documentado (bootstrap Admin REST), jamás para usuarios finales. | REQ-SEC-001, ADR-010/015 |
| D-CP-07 | **MfaGate como dependencia de router**: toda ruta de negocio exige `session.mfa_verified` o 403 `mfa_required`; exentas: `/healthz` y `/api/auth/*`. La evidencia MFA es dual (D-MFA-01, spec 011): claim `amr` con `mfa`/`otp` **O** `acr` numérico ≥ `TRUST_MFA_MIN_ACR` (default 2) — Keycloak 26 no emite `amr` (verificado en vivo); fail-closed sin evidencia. | REQ-ORG-007, arq. §3.1, spec 011 |
| D-CP-08 | **RBAC data-driven**: los permisos del principal se calculan por request desde `user_roles` + `catalog.role_permissions` (la matriz §2.1 vive en datos, I10). `require_permission(p)` → 403 `permission_denied` si falta. `dept_admin` nunca ve/edita fuera de su departamento (los IDs cruzados caen fuera de su filtro o devuelven 404). | REQ-ORG-003/004, spec 001 §2.1 |
| D-CP-09 | **Cross-tenant → 404** (no filtrable): RLS hace que los IDs de otro tenant simplemente no existan; la API traduce "no encontrado" a 404 sin revelar existencia. | arq. §5.2, REQ-TEN-002 |
| D-CP-10 | **Jobs**: enqueue idempotente por `(tenant_id, job_type, idempotency_key)`; claim cross-tenant con `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)` en una conexión **`trust_migrator`** dedicada SOLO al claim (el schema no ofrece vía cross-tenant para `trust_app` — RLS bloquea la cola); el procesamiento SIEMPRE corre como `trust_app` con `set_config` del tenant del job. Alternativa rechazada por ahora: función `SECURITY DEFINER claim_next_job()` (requeriría tocar el schema canónico de spec 002; se documenta como evolución). | REQ-ORG-002, C2, D-DB-02 |
| D-CP-11 | **Alembic**: `platform/alembic.ini` apunta con `script_location` al proyecto canónico `db/migrations` (dueño lógico: spec 002). NO existe una segunda línea de migraciones; la paridad schema.sql↔Alembic de spec 002 sigue siendo la única verdad del schema. | D-DB-12 |
| D-CP-12 | **Rate limiting de login diferido** con razón documentada: contar fallos por cuenta/IP exige una tabla persistida accesible pre-autenticación (cross-tenant), que el schema de spec 002 no tiene; `audit_log` no sirve (exige `tenant_id` confiable y es append-only por tenant). Se crea issue de seguimiento para evolución de capa de datos + spec de seguridad. | REQ-SEC-006 (diferido) |
| D-CP-13 | **Device token**: JWT HS256 firmado por el control plane (secreto dedicado por env, algoritmo pineado en verify — sin confusion), claims `device_id/user_id/tenant_id/jti`, TTL corto (15 min default); `DeviceGate` valida firma + TTL + estado `active` en DB por request (sin cache en v1: revocación inmediata; la cache ≤ 30 s de ADR-013 se añade si el perfil de carga lo pide). | ADR-013, §5.1 |
| D-CP-14 | **Envelope encryption (primitiva)**: DEK aleatoria por credencial (AES-256-GCM) cifra el secreto; la KEK (env, fuera de DB) cifra la DEK. Nonce único por credencial reutilizado para ambos cifrados (claves distintas ⇒ sin colisión de nonce); `key_id` versiona la KEK. | ADR-009, D-DB-08 |
| D-CP-15 | **Combinador deny-overrides puro y testeado**: `combine(org, dept, user, catalog)` — booleanos por AND; listas de allow por intersección (nivel que define `[]` vacía todo); enteros por mínimo; strings por ranking de restrictividad documentado (`shared_agent_grants_mode: explicit>open`, `artifact_sharing: private>shared`); maps (mensajerías) por plataforma (`allowed=false` gana; modo por ranking `read_only>reply>full`). Las claves `invariable=true` del catálogo SIEMPRE resuelven a su `default_value` (piso del proveedor). Clarificación (fix t_f0d3122d): la clave ausente en los 3 niveles resuelve al `default_value` del catálogo — para `allowed_models` ese default es `null` (migración 0019), de modo que "ausente = heredar/sin restricción" es distinguible de "definida `[]` = deny-all explícito" tanto en la policy efectiva (`GET /api/me/policies`) como en el enforcement; los consumidores de claves de lista NO deben colapsar `null`/ausente con `[]` (patrón `or []` + truthiness prohibido para gates). | REQ-POL-002/004, arq. §5.7, t_f0d3122d |
| D-CP-16 | **Envelope de errores** único (arq. §5.2): `{"error": {code, reason, message, request_id}}`; `request_id` generado por middleware (contextvar) y devuelto en headers `X-Request-Id`. Reasons estables de §5.2 + `permission_denied` (adicional, documentado aquí como extensión del contrato para denegación RBAC genérica). | arq. §5.2, spec 001 §9.8 |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ con AC verificable con evidencia real. Trazabilidad: `[001-REQ-…]` = spec maestra, `[ARQ-…]` = arquitectura, `[002-…]` = spec datos.

### 3.1 Proyecto y operación

**REQ-CP-001** (M) [ARQ-§3.1/§3.10] — Cuando un desarrollador arranca el control plane localmente, el sistema expone `uvicorn app.main:app` en `127.0.0.1:8000` con `GET /healthz` que verifica la conexión a PostgreSQL como `trust_app` y responde `{"status":"ok","database":"up"}`.
> **AC-CP-001**: proceso real levantado con curl a `/healthz` → 200 con ambos campos; con PostgreSQL caído → 503.

**REQ-CP-002** (M) [constitution VI] — Cuando se configura el servicio, el sistema lee TODA su configuración de variables de entorno (`DATABASE_URL`, `DATABASE_MIGRATOR_URL`, `IDP_BASE_URL`, `OIDC_AUDIENCE`, `SESSION_TTL_MINUTES`, `DEVICE_TOKEN_SECRET`, `VAULT_KEK_B64`, …) vía pydantic-settings; sin `DATABASE_URL` no arranca (fail-closed).
> **AC-CP-002**: arranque sin `DATABASE_URL` → error explícito; con env completo → settings cargadas (test).

**REQ-CP-003** (M) [ARQ-§5.2] — Cuando cualquier handler falla, el sistema responde el envelope único `{"error":{code, reason, message, request_id}}` con el `reason` estable correspondiente y el mismo `request_id` del header `X-Request-Id`.
> **AC-CP-003**: 401/403/404/422 provocados en tests devuelven el envelope con `reason` correcto y `request_id` no vacío igual al header.

**REQ-CP-004** (M) [D-CP-11, 002-D-DB-12] — Cuando se versiona el schema desde `platform/`, el sistema usa el proyecto Alembic canónico de `db/migrations` vía `platform/alembic.ini` (`script_location` relativo); no existe directorio de versions propio.
> **AC-CP-004**: `alembic -c platform/alembic.ini upgrade head` sobre DB limpia produce el mismo schema que el runbook de `db/README.md` (verificado en la suite: la DB de tests se levanta exactamente así).

### 3.2 Middleware multi-tenant

**REQ-CP-010** (M) [001-REQ-TEN-001/002, 002-D-DB-02] — Cuando el API ejecuta cualquier query de negocio, el sistema la ejecuta dentro de una transacción con `set_config('app.tenant_id', <tenant>, true)` como `trust_app`; ninguna ruta accede datos sin scope.
> **AC-CP-010**: test de introspección: durante un request autenticado, `current_setting('app.tenant_id', true)` == tenant del principal y `SELECT` sin filas ajenas; sin autenticación, las rutas de negocio → 401 antes de abrir transacción de negocio.

**REQ-CP-011** (M) [001-REQ-TEN-006] — Cuando el tenant del principal está `suspended`, el sistema rechaza toda ruta de negocio con 403 `reason=tenant_suspended` conservando los datos.
> **AC-CP-011**: suspender el tenant (UPDATE directo) → el siguiente request con sesión válida → 403 `tenant_suspended`; reactivar → acceso restaurado.

**REQ-CP-012** (M) [001-REQ-TEN-002, D-CP-09] — Cuando un principal referencia IDs de otro tenant (forjados), el sistema responde 404 sin revelar existencia.
> **AC-CP-012**: job de otro tenant → 404; usuario de otro tenant → 404 (RLS: la fila no existe para el scope).

### 3.3 Auth OIDC y sesiones

**REQ-CP-020** (M) [001-REQ-SEC-001, ARQ-ADR-015] — Cuando un cliente (desktop/console) completa el flujo authorization-code + PKCE S256 contra el realm del tenant y presenta el `id_token`, el sistema lo verifica contra JWKS (firma RS256/ES256, `exp`, `iat`, `aud`, `iss`, `sub`, claim `tenant_id`) y, si el usuario existe y está `active`, minta una sesión en PostgreSQL.
> **AC-CP-020**: e2e contra Keycloak real (realm dedicado de test, PKCE completo con code_verifier): `POST /api/auth/session` → 201 con sesión; usuario sin `oidc_subject` registrado → 401 `invalid_token`.

**REQ-CP-021** (M) [001-REQ-SEC-002] — Cuando el sistema recibe un JWT inválido (firma rota, `exp` vencido, `aud` incorrecto, `iss` incorrecto, HS256, sin claim `tenant_id`), lo rechaza con 401 `invalid_token` sin indicar qué claim falló.
> **AC-CP-021**: los 6 tokens maliciosos → 401 con envelope `invalid_token` (test unitario contra JWKS mock real servido por HTTP).

**REQ-CP-022** (M) [001-REQ-SEC-003, C2] — Cuando se minta una sesión, el sistema persiste en `sessions` el hash del token (`jti`), hash de refresh, hash de CSRF, `mfa_verified` (desde `amr` o `acr` ≥ `TRUST_MFA_MIN_ACR` — D-MFA-01, spec 011), `kind`, `expires_at` server-side y estado; el token en claro no se almacena jamás; una sesión sobrevive el reinicio del API.
> **AC-CP-022**: tras mintar, `SELECT` de `sessions` no contiene el token en claro (búsqueda del valor = 0); nueva instancia de app (engine nuevo) acepta el mismo token (test de "restart").

**REQ-CP-023** (M) [001-REQ-SEC-004] — Cuando un refresh token se canjea, el sistema rota el hash (one-time); el canje de un refresh ya usado revoca la sesión (detección de reuso) y responde 401.
> **AC-CP-023**: refresh #1 → 200 con nuevo refresh; replay del refresh #1 → 401 y la sesión queda `revoked`.

**REQ-CP-024** (M) [001-REQ-ORG-007] — Cuando un principal sin MFA verificada (`mfa_verified=false`) toca una ruta de negocio, el sistema responde 403 `reason=mfa_required`; las rutas `/api/auth/*` y `/healthz` están exentas.
> **AC-CP-024**: sesión mintada de un token sin evidencia MFA (sin `amr` y `acr` < umbral) → `GET /api/me` → 403 `mfa_required`; con `amr=["mfa"]` o `acr:"2"` (forma real KC26, D-MFA-01 spec 011) → 200.

**REQ-CP-025** (M) [001-REQ-SEC-010] — Cuando la sesión es de consola (`kind=admin`), el sistema entrega cookie `trust_session` con flags `Secure`+`HttpOnly`+`SameSite=Lax` y exige `X-CSRF-Token` (hash almacenado) en toda mutación; el desktop usa `Authorization: Bearer` sin cookie.
> **AC-CP-025**: inspección de `Set-Cookie` cumple los 3 flags; POST sin `X-CSRF-Token` con cookie → 403 `csrf_required`; con Bearer no se exige CSRF.

**REQ-CP-026** (M) [001-REQ-SEC-001] — Cuando el API maneja credenciales, jamás acepta ni transporta passwords de usuarios finales ni usa `grant_type=password` (verificación por grep + revisión: el único password grant del sistema es `admin-cli` del bootstrap interno de Keycloak, fuera del API).
> **AC-CP-026**: `grep -r "grant_type=password" platform/app/` → 0; el flujo funcional de test es code+PKCE (AC-CP-020).

### 3.4 RBAC

**REQ-CP-030** (M) [001-REQ-ORG-003, spec 001 §2.1] — Cuando una ruta declara `require_permission(p)`, el sistema concede acceso solo si algún rol del principal tiene `p` en `catalog.role_permissions`; si no, 403 `permission_denied`.
> **AC-CP-030**: test de matriz generado desde `catalog.role_permissions` (datos, no constantes): para cada (rol, permiso) de la matriz, un principal con ese rol obtiene el resultado esperado en un endpoint sondeo por permiso.

**REQ-CP-031** (M) [001-REQ-ORG-004] — Cuando un `dept_admin` opera, el sistema limita su alcance a los departamentos de sus asignaciones `dept_admin`; los recursos de otros departamentos del mismo tenant → 404/403, y los de otros tenants → 404.
> **AC-CP-031**: dept_admin del depto A2 lista jobs/usuarios: solo su depto; ID del depto A1 → 404.

**REQ-CP-032** (M) [001-REQ-ORG-005 (parcial)] — Cuando una sesión se revoca (logout), el sistema invalida el token de sesión y su refresh de inmediato (estado en DB).
> **AC-CP-032**: logout → request siguiente con el mismo Bearer → 401; refresh → 401. (La revocación en cascada completa —keys LiteLLM, dispositivos— es de spec 005.)

### 3.5 Jobs

**REQ-CP-040** (M) [001-REQ-ORG-002, C2] — Cuando se encola un job, el sistema lo persiste en `jobs` con `tenant_id`, tipo, payload, `idempotency_key` única por (tenant, tipo) y estado consultable por API dentro del tenant.
> **AC-CP-040**: `POST /api/jobs` (o servicio) dos veces con la misma clave → 1 fila (devuelve la existente); `GET /api/jobs/{id}` del mismo tenant → estado; de otro tenant → 404.

**REQ-CP-041** (M) [ARQ-§3.8, D-CP-10] — Cuando el worker (`python -m app.jobs.worker`) reclama jobs pendidos, el sistema los reclama uno a uno con `FOR UPDATE SKIP LOCKED` (sin doble claim bajo concurrencia), los ejecuta como `trust_app` con scope del tenant del job y registra resultado/error con reintentos (`attempts <= max_attempts`).
> **AC-CP-041**: dos claimers concurrentes sobre N jobs → cada job reclamado exactamente una vez; job con handler que falla → `failed` tras `max_attempts` con `error` poblado; job exitoso → `succeeded` con `result`.

**REQ-CP-042** (M) [C2] — Cuando el API se reinicia con jobs en cola, el sistema no pierde estado: los jobs siguen reclamables y las sesiones siguen válidas (cero estado en memoria).
> **AC-CP-042**: jobs encolados + sesiones activas → nueva instancia de app/worker → jobs se procesan y sesiones aceptadas (test con engine nuevo).

### 3.6 Servicios transversales

**REQ-CP-050** (M) [001-REQ-AUD-002/007, 002-D-DB-07] — Cuando un módulo emite un evento de audit (`audit.emit`), el sistema lo INSERTa en `audit_log` (el trigger del schema asigna `seq`/hash chain) y ofrece `POST /api/admin/audit/verify` (org_admin) que invoca `audit_verify_chain(tenant)` devolviendo `{status:"ok"}` o `{status:"broken", seq:N}`.
> **AC-CP-050**: emit de 3 eventos → verify → `ok`; los 3 eventos consultables con `seq` 1..n consecutivos (la manipulación/tamper es de spec 008).

**REQ-CP-051** (M) [001-REQ-POL-002/004/006] — Cuando se calcula la política efectiva de un usuario (`GET /api/me/policies`), el sistema combina los 3 niveles con deny-overrides (D-CP-15) y fuerza las invariables del proveedor a su valor de piso.
> **AC-CP-051**: matriz de combinación (org permite/depto prohíbe; org prohíbe/depto permite; ambas permiten; invariable `require_mfa=false` en cualquier nivel → efectivo `true`) — tests unitarios del combinador + endpoint devuelve la efectiva del principal.

**REQ-CP-052** (M) [001-REQ-VLT-001, ADR-009] — Cuando el vault cifra una credencial (primitiva `vault.crypto`), el sistema genera DEK aleatoria por credencial, cifra el secreto con AES-256-GCM, cifra la DEK con la KEK del entorno y devuelve `(ciphertext_secret, ciphertext_dek, nonce, key_id)`; sin la KEK correcta el descifrado es imposible.
> **AC-CP-052**: roundtrip exacto; descifrado con KEK distinta → fallo explícito; el secreto canario no aparece en ningún ciphertext (búsqueda = 0).

**REQ-CP-053** (M) [001-REQ-DEV-003, ADR-013] — Cuando una ruta exige dispositivo (`require_device`), el sistema valida el `X-Device-Token` (firma del control plane, TTL, tenant) y el estado `active` del dispositivo en DB; en otro caso 401 `device_not_authorized`.
> **AC-CP-053**: token válido + dispositivo `active` → 200; dispositivo `revoked` (UPDATE directo) → 401 `device_not_authorized` en el request siguiente (sin cache); token vencido/firmado con otro secreto → 401.

**REQ-CP-054** (M) [001-REQ-CON-001, REQ-MDL-006] — Cuando un cliente autenticado lista catálogos globales (`GET /api/models`, `GET /api/connectors`), el sistema devuelve las filas sembradas de `catalog` (solo lectura, sin datos de tenant).
> **AC-CP-054**: `GET /api/connectors` → los 6 conectores iniciales; `GET /api/models` → ≥ 2 modelos con pricing en centavos.

## 4. Contrato de superficie v1 (rutas del núcleo)

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/healthz` | pública | Liveness + DB check |
| POST | `/api/auth/session` | body `{id_token, kind}` | Verifica JWT (JWKS) y minta sesión (cookie si `kind=admin`) |
| POST | `/api/auth/refresh` | body `{refresh_token}` | Rotación one-time con detección de reuso |
| POST | `/api/auth/logout` | Bearer/cookie | Revoca la sesión |
| GET | `/api/me` | Bearer/cookie + MFA | Principal: usuario, roles, permisos, tenant |
| GET | `/api/me/policies` | Bearer/cookie + MFA | Política efectiva (deny-overrides) |
| GET | `/api/jobs/{job_id}` | Bearer/cookie + MFA | Estado del job (scope tenant; cross → 404) |
| POST | `/api/admin/audit/verify` | org_admin | Verificación de hash chain del tenant |
| GET | `/api/models` | Bearer/cookie + MFA | Catálogo global de modelos |
| GET | `/api/connectors` | Bearer/cookie + MFA | Catálogo global de conectores |

## 5. Checklist de convergencia

- [x] AC-CP-001…054 verificados con evidencia real (tests + proceso real).
- [x] Suite `platform/tests/` en verde sobre PostgreSQL 17 real (contenedor `trust-db`), incluyendo e2e PKCE contra Keycloak real — **80 passed** (2026-10-23).
- [x] Aislamiento multi-tenant verificado desde el API (cross-tenant → 404; scope por request con test de no-fuga del pool; sin scope → 0 filas).
- [x] Matriz RBAC verificada desde datos del catálogo (54 combinaciones rol×permiso leídas de `catalog.role_permissions`, no constantes).
- [x] Cero estado de sesión/job en memoria (tests de restart con engine nuevo: sesiones y jobs sobreviven).
- [x] `grep` de `verify=False` y `grant_type=password` en `platform/app/` → 0 ocurrencias (el único password grant del sistema es `admin-cli` del bootstrap Admin REST, documentado en ADR-015 y fuera del API).
- [x] Trazabilidad REQ↔AC: cada REQ-CP tiene sus AC referenciados y cada test nombra su AC en el docstring.
- [x] tasks.md cerrado; desviaciones vs arquitectura §3.1/§5: ninguna semántica (MfaGate/RBAC/DeviceGate implementados como dependencias de router FastAPI en vez de middleware ASGI — mismo punto de enforcement, orden equivalente a §3.1; documentado en plan §2).

Evidencia de la corrida de convergencia (2026-10-23): `pytest tests/ -q` →
**80 passed** (health/envelope, JWKS 6 negativos contra JWKS real por HTTP,
sesiones mint/refresh/replay/logout/restart, MFA+CSRF con cookie Secure,
RBAC data-driven, aislamiento + tenant suspendido, jobs con worker real en
subproceso y claim concurrente sin dobles, audit chain ok, deny-overrides,
vault crypto, device gate, catálogos, e2e PKCE contra Keycloak 26 real con
realm aprovisionado por Admin REST); `uvicorn app.main:app` en 127.0.0.1:8000 →
`/healthz` 200 `{"status":"ok","database":"up"}` + envelope en 401s con
`X-Request-Id`; suite `db/` re-verificada tras los cambios: **153 passed**.

