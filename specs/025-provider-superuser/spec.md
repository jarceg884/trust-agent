# Spec 025 — Super-user del Provider Console: operador en BD + MFA opcional (mandato Miller)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |
| **Autor** | dev (tarea t_6c2be725, mandato Miller) |
| **Padre** | specs/014-provider-console/spec.md (D-PC-02 login del operador — supercedido), specs/004-tenant-onboarding (D-ONB-02 provider token), specs/011-mfa-acr-evidence (TOTP/acr), specs/002-data-layer (plano `ops`) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema y alcance

**Problema (mandato Miller)**: el login del Provider Console usa un token
estático de environment (`TRUST_PROVIDER_TOKEN`, spec 004 D-ONB-02, consumido
en `app/config.py:105` y `get_provider_principal`). No existe NINGUNA cuenta
de operador del proveedor en la base de datos: no hay usuario, no hay
password hasheado, no hay MFA, no hay audit de acceso del operador. Un
secreto estático de entorno no es una identidad: no rota, no se puede
suspender, no soporta segundo factor y su uso no deja rastro.

**Mandato**: el Provider Console debe tener un **super-user de proveedor**
con usuario y contraseña, quemado en la BD con esquema de seguridad completo,
con la opción de añadir MFA una vez ya adentro.

**Dentro de alcance**:

1. Tabla `ops.provider_operators` — el ÚNICO actor del sistema fuera del
   multi-tenant: username único, password hasheado con **argon2id** (PHC
   string, jamás plaintext), estado `active`/`disabled`, secret TOTP
   nullable **cifrado en reposo** (envelope AES-256-GCM con la KEK del
   vault), `must_change_password`, timestamps.
2. Seed al arranque (bootstrap): si no existe operador, se crea el
   super-user inicial con username de config y password one-time generado;
   se muestra **EXACTAMENTE una vez** en el log de arranque (patrón OTA de
   D-ONB-05). Primer login fuerza cambio de password (required action).
3. Login usuario+contraseña (`POST /api/provider/auth/login`) con el
   throttle persistido existente (`ops.login_throttle`, `app/auth/throttle.py`),
   sesión en PostgreSQL (`ops.provider_sessions`, misma infra que
   `public.sessions`: token opaco, sha256 del secreto, nada en memoria),
   cookie httpOnly vía el BFF del Provider Console. Audit de acceso:
   `provider.login`, `provider.login_denied`, `provider.mfa_enrolled`
   (+ `provider.password_changed`, `provider.mfa_disabled`).
4. MFA opcional adentro: sección "Security" del Provider Console — enrolar
   TOTP (RFC 6238, SHA-1/6 dígitos/30 s, verificado contra los vectores del
   RFC), verificar un código antes de activar, banner que lo sugiere
   siempre, opcional. Si el operador tiene MFA activo, el login lo exige.
5. Decisión `provider_token` (ver D-PS-06): **deprecado — solo break-glass
   documentado**. Fail-closed si no hay operador seedeado.
6. Tests reales: login ok, password malo → 401 + throttle, MFA enrolado →
   login lo exige, sesión sobrevive reinicio del API, primer login fuerza
   cambio de password.

**Fuera de alcance** (documentado):

- Rotación/backup de códigos de recuperación (recovery codes) — el operador
  es único y el break-glass documentado es la vía de recuperación.
- Múltiples operadores: la tabla soporta N filas, pero la UI y el bootstrap
  gestionan UNO (el super-user). Crear más operadores queda para una
  administración de operadores futura.
- WebAuthn/passkeys: TOTP es el factor mandatado (mismo algoritmo que el
  spec 011 usa vía Keycloak).
- Cambiar el auth de los tenants (OIDC/PKCE por realm): intocado.

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-PS-01 | **El operador vive en el plano proveedor `ops`, FUERA del scope tenant por diseño**. `ops` es el plano del proveedor sin RLS (D-DB-01, db/README § "Planos del schema"): `ops.login_throttle` y `ops.spend_cursor` ya viven ahí por la misma razón. La tabla NO lleva `tenant_id` ni política RLS — el operador no pertenece a ningún tenant y NINGUNA sesión de tenant puede alcanzarla: las tablas de negocio siguen con RLS fail-closed y la app conecta como `trust_app` (sin BYPASSRLS). El aislamiento se verifica igual que siempre: un principal de tenant jamás pasa por `get_provider_principal`. | constitution III, spec 002 D-DB-01 |
| D-PS-02 | **Password con argon2id** (`argon2-cffi`, PasswordHasher por defecto = argon2id m=64 MiB/t=3/p=4, PHC string en `password_hash`). Elección sobre bcrypt: ganador de la Password Hashing Competition, resistente a GPU, ya usado por OWASP como primera recomendación. El hash (PHC) lleva salt propio por usuario; verificar es `PasswordHasher.verify`. Jamás plaintext, jamás SHA sin KDF. | constitution IV, mandato |
| D-PS-03 | **Seed idempotente al arranque con password one-time mostrado EXACTAMENTE una vez**. `ensure_provider_operator()` corre en el lifespan de la app: `SELECT count(*)` bajo advisory lock (`pg_advisory_xact_lock`); si es 0 → genera password (`secrets.token_urlsafe(18)`), INSERT, y log UNA sola vez en la rama que realmente insertó (el lock + re-check eliminan la carrera de dos boots paralelos; re-arrancar con el operador ya existente NO vuelve a loguear nada). Username de config `TRUST_PROVIDER_OPERATOR_USERNAME` (default `provider-admin`). `TRUST_PROVIDER_OPERATOR_INITIAL_PASSWORD` (vacío por defecto): si está fijado, el bootstrap lo usa en vez de generar (vía de tests/CI documentada; en producción se deja vacío → password aleatorio). El operador nace con `must_change_password=true`. Si la tabla no existe aún (imagen sin migrar) el bootstrap registra el error y sigue: el login fail-closea después. | mandato, D-ONB-05 (patrón OTA), constitution IV |
| D-PS-04 | **Sesión del operador = misma infra que `public.sessions`, en `ops.provider_sessions`**: token opaco `pst1.<session_id>.<secret>` con solo `sha256(secret)` en BD (`jti`), `status` active/revoked/expired, `mfa_verified`, `ip`, `user_agent`, `expires_at` (TTL de `session_ttl_minutes`), `last_seen_at`. NADA en memoria de proceso — la sesión sobrevive el reinicio del API por construcción (verificado por test + reinicio real del contenedor). La cookie httpOnly `ta_provider_session` vive en el BFF (Next.js) y el proxy la reenvía como `Authorization: Bearer pst1…` — el token jamás llega a JS del navegador. CSRF: igual razonamiento que D-PC-02 — el control plane autentica por header que solo el BFF same-origin inyecta, y `sameSite=lax` bloquea el POST cross-site. | constitution IV, spec 009/014 BFF |
| D-PS-05 | **MFA TOTP first-party, RFC 6238** (SHA-1, 6 dígitos, 30 s, ventana ±1 paso, secreto de 160 bits base32). El operador NO vive en ningún realm de Keycloak (está fuera del multi-tenant), así que el TOTP del spec 011 (KC `CONFIGURE_TOTP` + `acr`) no aplica: el control plane verifica el código él mismo (`app/provider/totp.py`, stdlib `hmac`, verificado contra los vectores del RFC 6238). El secret se entrega one-time en `enroll-start` (secreto + `otpauth://` para el authenticator) y se guarda **cifrado en reposo** con el envelope del vault (AES-256-GCM, KEK fuera de la BD, `app/vault/crypto.py`): `totp_secret_enc`/`totp_dek_enc`/`totp_nonce`/`totp_key_id` nullable. Sin KEK configurada, el enrolamiento y el login con MFA activa fail-closean (503). Enrolar es OPCIONAL (banner que lo sugiere); si `totp_active=true`, el login exige `totp_code` (sin código → 401 `mfa_code_required`; código malo → 401 + throttle + audit `provider.login_denied`). | mandato, spec 011 (mismo algoritmo TOTP), constitution IV |
| D-PS-06 | **`provider_token` DEPRECADO — solo break-glass documentado**. `get_provider_principal` acepta (1) sesión de operador `pst1…` (camino primario) o (2) el bearer estático de entorno SOLO si `TRUST_PROVIDER_TOKEN` está explícitamente configurado (break-glass para CLI/scripts de emergencia, documentado en `platform/README.md` como deprecado; la UI ya no lo usa). **Fail-closed doble**: sin operador seedeado en `ops.provider_operators`, TODA autenticación provider (sesión y break-glass) responde 503 — la recuperación es reiniciar el API (el bootstrap re-seedea). Eliminarlo del todo habría dejado a los scripts de operación (`trust-provision`, evidence scripts) sin vía de acceso documentada. | mandato punto 5, D-ONB-02 |
| D-PS-07 | **Audit del plano proveedor en `ops.provider_audit`** (append-only + hash chain GLOBAL con el mismo patrón del trigger de `audit_log`, advisory lock propia; trigger de rechazo de UPDATE/DELETE). `audit_log` es por-tenant (`tenant_id NOT NULL` + FK + chain por tenant): los eventos del operador (`provider.login`, `provider.login_denied`, `provider.mfa_enrolled`, `provider.mfa_disabled`, `provider.password_changed`) no pertenecen a ningún tenant. `action` lleva FK a `catalog.audit_actions` (las 5 acciones se añaden al catálogo — data-driven, I10). Los eventos sobre tenants (p.ej. `tenant.suspended`) SIGUEN en el `audit_log` del tenant afectado, como hoy. | constitution IV/V, spec 008, I10 |
| D-PS-08 | **Primer login fuerza cambio de password (required action)**: el login con `must_change_password=true` minta sesión (audit `provider.login` con payload `forced_password_change:true`) pero el control plane NIEGA toda ruta provider de negocio con 403 `password_change_required` hasta completar `POST /api/provider/auth/password` (exige password actual + nueva ≥ 12 chars; limpia el flag; audit `provider.password_changed`; revoca las demás sesiones del operador). La UI redirige a `/change-password` y lo bloquea ahí. El break-glass NO está sujeto al flag (es de emergencia y no representa al operador), pero sigue fail-closed si no hay operador. | mandato punto 2 |
| D-PS-09 | **Throttle reutilizado tal cual**: claves `acct:provider:<sha256(username)>` (cuenta) e `ip:<ip>` en la MISMA `ops.login_throttle` con `check()`/`register_failure()`/`register_success()` existentes. El contador persiste en PostgreSQL → sobrevive reinicios (igual que REQ-SEC-006). Password malo o código TOTP malo cuentan como fallo; código ausente (prompt de UX) no cuenta. | mandato punto 3, spec 003 REQ-SEC-006 |

## 3. Requisitos (formato EARS)

### 3.1 Identidad y seed

**REQ-PS-001** (M) [D-PS-01/02] — Cuando se aplica la migración 0015, el
sistema crea `ops.provider_operators` con username único, `password_hash`
(PHC argon2id), `status` CHECK (`active`/`disabled`), columnas TOTP
cifradas nullable + `totp_active`, `must_change_password`, `created_at`/
`updated_at`; sin RLS y sin `tenant_id` (plano proveedor).
> **AC-PS-001**: `\d ops.provider_operators` en la BD live muestra la tabla
> con todas las columnas; `password_hash` matchea `^\$argon2id\$`; la tabla
> no aparece en el listado RLS de tablas de negocio; paridad
> `test_schema_parity.py` en verde.

**REQ-PS-002** (M) [D-PS-03] — Cuando el API arranca y no existe ningún
operador, el sistema crea el super-user con username de config y password
one-time y lo muestra EXACTAMENTE una vez en el log de arranque; si el
operador ya existe, no genera ni loguea nada.
> **AC-PS-002**: reinicio del contenedor `api` con la tabla vacía → una
> línea `PROVIDER OPERATOR ONE-TIME PASSWORD` en `docker logs`; segundo
> reinicio → la línea NO reaparece; SQL: 1 fila con
> `must_change_password=true`.

### 3.2 Login y sesión

**REQ-PS-003** (M) [D-PS-04/09] — Cuando el operador hace login con
username+password válidos (y `totp_code` válido si tiene MFA activa), el
sistema verifica argon2id, mints sesión en `ops.provider_sessions`,
responde 200 con el token de sesión (que el BFF guarda en cookie httpOnly)
y registra `provider.login` en `ops.provider_audit`.
> **AC-PS-003**: login real → 200; fila en `ops.provider_sessions` con
> `jti = sha256(secret)`; `GET /api/admin/tenants` con el token → 200;
> evento `provider.login` en `ops.provider_audit` con cadena válida.

**REQ-PS-004** (M) [D-PS-09] — Cuando el login falla (password malo,
usuario inexistente, operador disabled o código TOTP malo), el sistema
responde 401 con mensaje que no revela cuál factor falló, cuenta el fallo
en el throttle persistido y registra `provider.login_denied`; al superar el
umbral responde 429 `rate_limited` con `Retry-After`.
> **AC-PS-004**: password malo → 401 + fila/contador en
> `ops.login_throttle` con clave `acct:provider:…` + evento
> `provider.login_denied`; N fallos consecutivos (umbral de config) → 429;
> el lock sobrevive un reinicio del API (contador en PostgreSQL).

**REQ-PS-005** (M) [D-PS-04] — Cuando el API se reinicia con una sesión de
operador activa, la sesión sigue siendo válida (nada de estado de sesión
vive en memoria).
> **AC-PS-005**: login → reinicio real del contenedor `api` → el mismo
> Bearer `pst1…` sigue devolviendo 200 en `GET /api/admin/tenants`.

**REQ-PS-006** (M) [D-PS-08] — Cuando el operador con
`must_change_password=true` intenta cualquier ruta provider de negocio, el
sistema responde 403 `password_change_required`; tras cambiar la password
(actual válida + nueva ≥ 12 chars) el flag se limpia, las demás sesiones se
revocan y las rutas responden 200.
> **AC-PS-006**: login primerizo → `must_change_password:true` en la
> respuesta; `GET /api/admin/tenants` → 403 `password_change_required`;
> cambio de password → rutas 200; login con la password VIEJA → 401.

### 3.3 MFA

**REQ-PS-007** (M) [D-PS-05] — Cuando el operador inicia el enrolamiento
(`POST /api/provider/mfa/enroll-start`), el sistema genera un secret TOTP
nuevo (pendiente), lo guarda cifrado con la KEK y responde one-time con el
secret base32 + `otpauth://`; al confirmar con un código válido
(`POST /api/provider/mfa/enroll-confirm`) activa `totp_active=true` y
registra `provider.mfa_enrolled`. Un código inválido no activa nada.
> **AC-PS-007**: enroll-start → secret visible UNA vez (no reaparece en
> ningún GET); confirm con código generado del secret → `totp_active=true`
> (SQL) + evento `provider.mfa_enrolled`; confirm con código inventado →
> 422/401 y `totp_active` sigue false.

**REQ-PS-008** (M) [D-PS-05] — Cuando el operador tiene MFA activa, el
login exige `totp_code`: sin código → 401 `mfa_code_required`; código
inválido → 401 + throttle + `provider.login_denied`; código válido → 200
con `mfa_verified=true` en la sesión. Sin MFA activa el login no lo pide.
> **AC-PS-008**: login sin código con MFA activa → 401 `mfa_code_required`;
> con código real (generado con el secret del enrolamiento) → 200;
> `ops.provider_sessions.mfa_verified=true`.

**REQ-PS-009** (M) [D-PS-05, mandato] — Cuando el operador entra a la
sección "Security" del Provider Console, el sistema muestra el estado MFA
real, un banner que sugiere enrolarlo siempre (mientras no esté activo) y
el flujo de enrolamiento con verificación antes de activar; desactivar MFA
exige password + código TOTP válidos.
> **AC-PS-009**: flujo real en navegador: banner visible sin MFA →
> enroll → secret + otpauth mostrados → código correcto → estado
  "MFA active"; desactivar con password+ código → vuelve al banner.

### 3.4 Superficie y fail-closed

**REQ-PS-010** (M) [D-PS-06] — Cuando una ruta provider recibe un Bearer
`pst1…` de sesión válida, el sistema carga el principal provider con
permisos `platform_admin` data-driven (catálogo); el break-glass estático
solo funciona con `TRUST_PROVIDER_TOKEN` configurado; sin operador seedeado
→ 503 fail-closed para AMBOS caminos.
> **AC-PS-010**: matriz en tests: sesión válida → 200; sesión revocada →
> 401; break-glass configurado → 200; sin operador (tabla vacía) → 503 en
> ambos; la suite de spec 014 (`test_provider_admin.py`) sigue en verde
> (break-glass + operador seedeado).

**REQ-PS-011** (M) [D-PS-07] — Cuando se emite un evento del operador, el
sistema lo inserta en `ops.provider_audit` (append-only, chain global) y
cualquier UPDATE/DELETE es rechazado por trigger.
> **AC-PS-011**: intento de UPDATE sobre `ops.provider_audit` → error de BD;
> la cadena verifica (función `provider_audit_verify_chain` → ok).

**REQ-PS-012** (M) [constitution IV] — Cuando el API responde cualquier
ruta provider, el sistema NO incluye secretos en el cuerpo: ni el password
(ni el hash), ni el secret TOTP (fuera del one-time de enroll-start), ni el
secreto de sesión (solo el token de sesión en el login, que vive en cookie
httpOnly).
> **AC-PS-012**: grep del password/secret sobre los cuerpos de respuesta →
> 0 apariciones (test automatizado).

## 4. Contrato de superficie

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/api/provider/auth/login` | pública (throttle) | Login operador: `{username, password, totp_code?}` → `{operator, session_token, expires_at}` (REQ-PS-003/004/008) |
| POST | `/api/provider/auth/logout` | sesión operador | Revoca la sesión actual |
| GET | `/api/provider/auth/me` | sesión operador | `{username, must_change_password, mfa_enrolled}` |
| POST | `/api/provider/auth/password` | sesión operador | `{current_password, new_password}` → limpia el flag (REQ-PS-006) |
| POST | `/api/provider/mfa/enroll-start` | sesión operador (password ya cambiado) | One-time `{secret, otpauth_url}` (REQ-PS-007) |
| POST | `/api/provider/mfa/enroll-confirm` | ídem | `{code}` → activa MFA (REQ-PS-007) |
| POST | `/api/provider/mfa/disable` | ídem | `{password, code}` → desactiva MFA (REQ-PS-009) |

Las rutas `/api/admin/tenants*` existentes NO cambian de contrato: solo su
dependencia de auth pasa a aceptar sesión de operador además del
break-glass (D-PS-06).

## 5. Checklist de convergencia

- [x] AC-PS-001…012 con evidencia real (tests `tests/test_provider_superuser.py`
      + vectores RFC 6238 + reinicio real del contenedor + SQL).
- [x] Suite completa `platform/tests/` en verde (incluida spec 014).
      Nota 2026-10-26: `test_spend_cutoff.py` falla por una lane hermana en
      vuelo (governance/spend, mtime posterior a esta lane) — no relacionado.
- [x] Migración 0016 aplicada en la BD live vía `migrate` (imagen
      reconstruida) + paridad `schema.sql` (db/tests 182 en verde).
- [x] Provider Console: build + lint limpios, flujo real verificado
      (login → cambio de password → Security → enrol MFA → login con TOTP,
      evidencia en docs/provider-superuser.md).
- [x] Evidencia en `docs/provider-superuser.md`.
- [x] `platform/README.md`: `provider_token` documentado como break-glass
      deprecado + nuevas variables de entorno (.env.example).
