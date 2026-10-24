# Spec 011 — Evidencia MFA vía `acr` (D-MFA-01): Keycloak 26 no emite `amr`

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-24 |
| **Autor** | dev (tarea t_59afbe56, hallazgo del lane UI t_5ce74df8 / spec 009) |
| **Padre** | specs/003-control-plane/spec.md (D-CP-07, REQ-CP-022/024), specs/004-tenant-onboarding (provisioning), specs/007-devices (REQ-DEV-001a), specs/009-admin-console (D-AC-02 step-up), specs/010-desktop-enrollment |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Problema (bug confirmado en vivo)

La puerta MFA del control plane (`MfaGate`, D-CP-07) derivaba `mfa_verified`
**exclusivamente** del claim `amr` del `id_token`. Keycloak 26 **no emite `amr`
en absoluto** — verificado empíricamente (2026-10-23/24, realm real
`console-smoke`, login completo password+TOTP con enrolamiento real):

- `id_token` tras password+OTP: `amr: null`, `acr: "1"` (token real decodificado).
- Consecuencia: `mfa_verified=false` para **todo login real de KC26** → TODA la
  superficie de negocio responde 403 `mfa_required`. El Admin Console y el
  desktop no pueden obtener una sesión MFA-verificada contra un realm real.

**Por qué no lo detectó la suite**: el caso NEGATIVO e2e es real (login sin MFA
→ 403, correcto), pero el caso POSITIVO usa el IdP doble de tests (JWT
sintético con `amr=["mfa"]`), que no reproduce el comportamiento real de KC26.
El doble emitía `amr` porque se lo pedíamos — el bug estaba en el supuesto, no
en el doble.

**Pitfalls capturados**: skill `identity/keycloak-admin-oidc` #10–#12
(secreto TOTP alfanumérico con clave HMAC raw UTF-8; ausencia de `amr`;
wiring LoA/acr).

## 2. Alcance

**Dentro de alcance** (cambio transversal, ya integrado cross-lane):

- Extracción de evidencia MFA desde claims verificados: `amr` (OIDC estándar)
  **O** `acr` numérico ≥ `TRUST_MFA_MIN_ACR` — módulo `platform/app/auth/mfa.py`.
- Configuración del realm KC26 para que produzca `acr` significativo:
  `ensure_mfa_acr()` en `app/tenants/keycloak_admin.py`, invocada por el
  provisioning de tenants (spec 004, `app/tenants/provision.py`).
- Umbral por entorno `TRUST_MFA_MIN_ACR` (config.py) y su decisión de valor (§3).
- Tests de contrato de la puerta: `tests/test_mfa_acr.py` (unit + API).
- Trazabilidad: referencias cruzadas en specs 003/007 (D-CP-07, REQ-CP-022/024,
  AC-CP-024, plan 007 §2).

**Fuera de alcance**:

- Step-up `acr_values` del cliente (ya implementado por el console, spec 009
  D-AC-02; el desktop lo adopta en su lane).
- Cambios al schema de datos (no se toca DB).
- Emisión de `amr` por protocol mapper de KC (alternativa rechazada, §3).

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-MFA-01 | **Evidencia MFA dual, fail-closed**: `mfa_evidence(claims)` acepta (1) `amr` lista que contenga `mfa`/`otp` (OIDC estándar; lo emite el doble de tests y cualquier IdP que lo implemente) **O** (2) `acr` numérico ≥ `TRUST_MFA_MIN_ACR`. Todo lo demás (claims ausentes, `amr` no-lista, `acr` no-numérico tipo `"silver"`, floats) → `false`. Sin evidencia → 403 `mfa_required` (fail-closed inmutable). | REQ-ORG-007, D-CP-07 |
| D-MFA-02 | **El realm produce el `acr`**: `ensure_mfa_acr(realm)` (idempotente, Admin REST) configura (a) atributo de realm `acr.loa.map` = `{"1":0,"2":1}` (OJO: la constante real de KC es `acr.loa.map`, NO `acr-to-loa-map` — un nombre erróneo se ignora en silencio y el acr pedido se interpreta como LoA numérico crudo), (b) copia del flujo `browser` (el built-in no se puede modificar) cuyo sub-flujo "… Conditional OTP" lleva una condición REQUIRED "Condition - Level of Authentication" (nivel 1) y el OTP Form, con la condición copiada "Condition - user configured" **ELIMINADA** (`DELETE /authentication/executions/{id}`), y (c) la copia ligada como `browserFlow` del realm. **Por qué se elimina user-configured (hallazgo crítico, probe en vivo 2026-10-24)**: con AMBAS condiciones en el mismo sub-flujo condicional, Keycloak dispara el callback del LoA cuando la condición LoA apenas *coincide* — incluso si el flujo luego se salta por user-configured=false — y el login password-only de un usuario sin TOTP obtenía `acr:"2"` (fail-open). Con la condición LoA sola, el único camino a LoA 1 es el OTP Form; un usuario sin credencial TOTP no queda lockeado: `OTPFormAuthenticator.setRequiredActions()` fuerza `CONFIGURE_TOTP` inline (enrolamiento durante el login). Resultado verificado en vivo contra KC 26.2: password+TOTP → LoA 1 → `acr:"2"` en id_token y access_token; password-only → redirect a `required-action?execution=CONFIGURE_TOTP`, SIN code y jamás `acr:"2"`. | probe real 2026-10-23/24, skill keycloak26-amr-mfa |
| D-MFA-03 | **El provisioning SIEMPRE cablea el ACR**: `provision_tenant()` llama `kc.ensure_mfa_acr(slug)` — un realm sin wiring no puede producir sesiones MFA-verificadas, así que el wiring es parte del contrato de aprovisionamiento (spec 004), no una opción. | spec 004, D-ONB-* |
| D-MFA-04 | **`TRUST_MFA_MIN_ACR` = 2 en TODOS los entornos** (dev/staging/prod; default del código, sin override por entorno). Razón: con el mapa canónico de D-MFA-02, `acr:"2"` == password+OTP y `acr:"1"` == password-only. Bajarlo a 1 sería fail-open (aceptaría login sin segundo factor) — prohibido por constitución IV. Subirlo a ≥3 es imposible sin re-mapear el `acr.loa.map` del realm (bloquearía todo login): el umbral y el mapa LoA están **acoplados** y se cambian juntos, como un cambio de contrato versionado, nunca como knob de runtime. Documentado en `platform/README.md` (tabla de env). | constitución IV, D-MFA-02 |
| D-MFA-05 | **Alternativas rechazadas**: (a) protocol mapper que sintetice `amr` desde session notes — acopla el gate a configuración no estándar del realm y sigue dependiendo de un claim que KC no garantiza; (b) introspección de `auth_time`/session notes en el mint — convierte el mint en una llamada extra al IdP por login y no distingue OTP de re-auth. El `acr`/LoA es el mecanismo nativo de KC para expresar nivel de autenticación y viaja firmado en el token. | análisis t_59afbe56 |
| D-MFA-06 | **El doble de tests sigue emitiendo `amr`** (regresión del contrato estándar) y ADEMÁS permite `acr` arbitrario (`extra_claims`): la suite pina ambas señales y la forma real KC26 (`amr:null, acr:"2"`). El caso positivo e2e contra KC real queda cubierto por el smoke del console (spec 009) y el probe documentado en el skill. | gap de QA t_974c6a5c |

## 4. Requisitos (formato EARS)

**REQ-MFA-001** (M) [D-MFA-01, REQ-ORG-007] — Cuando el control plane evalúa
claims verificados para determinar evidencia MFA, el sistema acepta `amr`
(lista con `mfa`/`otp`) o `acr` numérico ≥ `TRUST_MFA_MIN_ACR`, y rechaza todo
otro valor como `false` (fail-closed).
> **AC-MFA-001**: `tests/test_mfa_acr.py` unit: `amr:["mfa"|"otp"|"pwd","otp"]`
> → true; `amr:["pwd"]|[]|"mfa"|{}|ausente` → false; `acr:"2"|2|"3"` → true;
> `acr:"1"|1|"0"|"silver"|"2.5"|""|None` → false.

**REQ-MFA-002** (M) [D-MFA-01, REQ-CP-022/024] — Cuando se minta una sesión
desde un `id_token` con forma real KC26 (`amr:null, acr:"2"`), el sistema
persiste `mfa_verified=true` y las rutas de negocio responden 200; con
`acr:"1"` (password-only) persiste `mfa_verified=false` y responde 403
`mfa_required`.
> **AC-MFA-002**: `tests/test_mfa_acr.py` API: mint con `acr:"2"` →
> `mfa_verified:true` + `GET /api/me` 200; con `acr:"1"` → 403 `mfa_required`.
> El camino JWT stateless (Bearer directo) evalúa `acr` igual.

**REQ-MFA-003** (M) [D-MFA-02/03, spec 004] — Cuando el proveedor
aprovisiona un tenant, el sistema configura el realm del tenant con el wiring
LoA/acr de D-MFA-02 (idempotente; re-ejecutar no rompe nada).
> **AC-MFA-003**: probe real (2026-10-24, realm `console-smoke2`): tenant
> aprovisionado con el código nuevo → login PKCE real + enrolamiento TOTP real
> → `id_token` con `acr:"2"` → mint `mfa_verified=true` → superficie de
> negocio accesible (smoke_console.py SMOKE PASS 21/21).

**REQ-MFA-004** (M) [D-MFA-01, REQ-DEV-001a, spec 007] — Cuando una ruta con
gate MFA propio (`POST /api/devices/enroll`) recibe un JWT con `acr:"2"`, el
sistema deja pasar la puerta MFA (el request procede a sus validaciones); con
`acr:"1"` responde 403 `mfa_required`.
> **AC-MFA-004**: `tests/test_mfa_acr.py`: enroll con `acr:"2"` + org_code
> erróneo → 422 (gate superado); con `acr:"1"` → 403 `mfa_required`.

**REQ-MFA-005** (M) [D-MFA-04] — Cuando se configura el servicio, el sistema
lee `TRUST_MFA_MIN_ACR` (default 2) y el valor documentado para todos los
entornos es 2; cambiarlo exige el cambio acoplado del `acr.loa.map` del realm
(contrato versionado, no knob de runtime).
> **AC-MFA-005**: `platform/README.md` documenta la variable con la decisión
> por entorno; `config.py` default 2; test de umbral lee el default.

**REQ-MFA-006** (M) [D-MFA-06] — Cuando la suite evalúa el contrato de la
puerta, el sistema pina ambas señales (`amr` estándar y `acr` KC26) incluida
la forma real KC26, de modo que un regresión en cualquiera de las dos falla la
suite.
> **AC-MFA-006**: `tests/test_mfa_acr.py` incluye `test_amr_still_accepted`
> (regresión `amr`) y `test_kc26_real_shape` (forma real KC26).

## 5. Checklist de convergencia

- [x] AC-MFA-001…006 verificados con evidencia real (tests + probe en vivo).
- [x] Suite `platform/tests/` completa en verde tras D-MFA-01 (ver §6).
- [x] Referencias cruzadas asentadas: spec 003 (D-CP-07, REQ-CP-022, AC-CP-024)
  y plan 007 §2 citan D-MFA-01/spec 011.
- [x] `TRUST_MFA_MIN_ACR` documentado por entorno (README + D-MFA-04).
- [x] Pitfalls del IdP real capturados en skill `identity/keycloak-admin-oidc`
  (#10–#12).
- [x] Fail-closed invariante: sin evidencia (ni `amr` ni `acr` suficiente) →
  403 `mfa_required` en TODOS los gates (sesión, JWT stateless, devices).

## 6. Evidencia de convergencia

- Probe real KC 26.2 (2026-10-23): realm default → `amr:null, acr:"1"` tras
  password+TOTP (el bug); realm con `ensure_mfa_acr()` → `acr:"2"`.
- Probe de seguridad del wiring (2026-10-24, t_c43906d1): con la condición
  "user configured" presente el login password-only sin TOTP obtenía
  `acr:"2"` (fail-open) — el wiring se corrigió para eliminarla (D-MFA-02);
  tras la corrección, password-only → inline `CONFIGURE_TOTP`, sin code.
- Smoke del console contra KC real (2026-10-24, lane UI, realm
  `console-smoke2`): `smoke_console.py` **SMOKE PASS 21/21** — login PKCE S256
  real + enrolamiento TOTP real + `acr:"2"` + mint `mfa_verified=true` +
  superficie de negocio accesible. (Realm re-cableado con el wiring
  corregido de D-MFA-02 tras el hallazgo del fail-open.)
- Aceptación en vivo (2026-10-24, t_c43906d1, realm `demo-org` + control
  plane real :8000): login PKCE real password+TOTP con `acr_values=2` →
  `id_token acr:"2"` → `POST /api/auth/session` 201 `mfa_verified=true` →
  `GET /api/me` 200.
- Suite completa `platform/tests/`: **226 passed** (2026-10-24, dos corridas
  back-to-back limpias sobre el árbol final con el wiring corregido, 37–43 s
  cada una; 213 previos + 13 de `tests/test_mfa_acr.py`; además 31/31 e2e
  contra el Keycloak 26 vivo en :8080, incluido
  `test_pkce_flow_mints_session_and_requires_mfa` — el camino real KC26).
  Nota: una corrida intermedia dio errores `ObjectInUse` en el DROP de la BD
  de test por colisión con otra sesión de pytest paralela (otro lane), no por
  regresión — re-verificado en verde sin procesos concurrentes.
