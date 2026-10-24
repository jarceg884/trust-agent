# Plan 011 — Evidencia MFA vía `acr` (D-MFA-01)

Spec: `specs/011-mfa-acr-evidence/spec.md` · Base: `platform/` (spec 003) ·
Contratos: specs/003 (D-CP-07, REQ-CP-022/024), 004 (provisioning), 007
(REQ-DEV-001a), 009 (D-AC-02).

## 1. Arquitectura de la implementación

```
platform/app/auth/
├── mfa.py                 # NUEVO: mfa_evidence(claims) — amr OR acr>=min (D-MFA-01)
├── service.py             # mint: mfa_verified = mfa_evidence(claims) (antes _amr_mfa)
└── router.py              # /api/me y sesión: mfa_evidence(claims)

platform/app/tenants/
├── keycloak_admin.py      # + ensure_mfa_acr(realm): acr.loa.map + flujo browser
│                          #   con condición LoA-1 en Conditional OTP (D-MFA-02)
└── provision.py           # provision_tenant() llama kc.ensure_mfa_acr(slug) (D-MFA-03)

platform/app/devices/
└── router.py              # gate de enroll usa mfa_evidence (antes amr-only)

platform/app/config.py     # + mfa_min_acr: int = 2 (TRUST_MFA_MIN_ACR, D-MFA-04)

platform/tests/
└── test_mfa_acr.py        # NUEVO: unit (claims) + API (mint/me/jwt/enroll)
```

## 2. Evaluación de evidencia (contracto D-MFA-01)

`mfa_evidence(claims)` — orden y semántica:

1. `_amr_mfa`: `amr` debe ser **lista**; true si algún elemento ∈ {`mfa`,`otp`}.
   String/dict/ausente → false (nunca confiar en formas no estándar).
2. `_acr_mfa`: `acr` coercionado a int; true si ≥ `settings.mfa_min_acr`.
   No-numérico (`"silver"`, `"2.5"`, `""`, None) → false (mapeos no cubiertos
   fallan cerrados).
3. OR de ambas. Sin evidencia → la sesión se minta con `mfa_verified=false` y
   `MfaGate` responde 403 `mfa_required` (fail-closed invariante, D-CP-07).

Puntos de evaluación (todos convergen en `mfa_evidence`): mint de sesión
(`service.py`), respuesta de sesión (`router.py`), `/api/me` (session y JWT
stateless) y gate de enroll de devices (`devices/router.py`).

## 3. Wiring del realm (D-MFA-02, Admin REST, idempotente)

1. `GET /admin/realms/{r}` → si `attributes["acr.loa.map"] != '{"1":0,"2":1}'`
   → `PUT` del realm con el atributo.
2. Si `browserFlow != "browser loa"` o falta la condición → `POST
   .../authentication/flows/browser/copy` (409 tolerado) → añadir ejecución
   REQUIRED "Condition - Level of Authentication" (config `loa-level=1`) al
   sub-flujo "… Conditional OTP".
3. `PUT /admin/realms/{r}` con `browserFlow = "browser loa"`.

Resultado (verificado en vivo KC 26.2): password+TOTP → LoA 1 → `acr:"2"`;
password-only → `acr:"1"`. Idempotente: re-ejecutar sobre un realm ya cableado
no hace nada destructivo (early-return tras verificar atributo + condición).

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| `acr` forjado por un IdP ajeno con LoA inflado | El `acr` viaja **firmado** en el token verificado por JWKS (D-CP-05); solo realms cableados por el provisioning (D-MFA-03) producen `acr:"2"` real. Un issuer aceptado que infla `acr` es un problema de confianza del issuer, igual que con `amr`. |
| Bajar `TRUST_MFA_MIN_ACR` a 1 = fail-open | D-MFA-04: valor pineado a 2 en todos los entornos; el cambio acoplado umbral↔`acr.loa.map` es un cambio de contrato versionado, documentado en README. |
| Realm existente aprovisionado ANTES del fix queda sin wiring | `ensure_mfa_acr` es idempotente y se llama en cada provisioning; para realms pre-existentes, re-ejecutar el provisioning (o la función) los cablea. Documentado en el runbook. |
| Copiar el flujo browser duplica flujos en re-runs | Early-return si `browserFlow` ya es la copia Y la condición LoA está configurada; el 409 del copy se tolera. |
| Regresión del camino `amr` (otros IdP / doble de tests) | `test_amr_still_accepted` + `test_kc26_real_shape` pinnan ambas señales (D-MFA-06). |
| QA positivo con JWT sintético vuelve a enmascarar el comportamiento real | El smoke del console (spec 009) es el guardián del camino real KC26; probe documentado en el skill `identity/keycloak-admin-oidc`. |
