# Tasks 011 — Evidencia MFA vía `acr` (D-MFA-01)

Spec: `specs/011-mfa-acr-evidence/spec.md` · Plan: `specs/011-mfa-acr-evidence/plan.md`

> Nota de origen: la implementación (T3–T5) llegó al árbol de forma
> cross-lane (sesión del lane desktop t_d3768316 + verificación en vivo del
> lane UI t_5ce74df8, 2026-10-24) ANTES del SDD formal. Esta tarea
> (t_59afbe56) formaliza el SDD, valida la suite completa y asienta la
> decisión del umbral — el orden implementación→spec se documenta aquí como
> desviación deliberada (el bug bloqueaba todo login real de KC26 y el fix
> fue verificado en vivo antes de escribirse la spec).

- [x] T1. Spec 011 (specify): problema, alcance, decisiones D-MFA-01…06,
  requisitos EARS REQ-MFA-001…006 + ACs. (t_59afbe56)
- [x] T2. Plan técnico (evaluación de evidencia, wiring del realm, riesgos).
- [x] T3. `app/auth/mfa.py`: `mfa_evidence(claims)` dual (amr OR acr≥min),
  fail-closed en todo lo demás; `config.py` `mfa_min_acr=2`. (AC-MFA-001/005)
- [x] T4. Integración: `auth/service.py` (mint), `auth/router.py` (sesión +
  `/api/me` session y JWT stateless), `devices/router.py` (gate enroll);
  `tenants/keycloak_admin.py` `ensure_mfa_acr()` + llamada desde
  `tenants/provision.py`. (AC-MFA-002/003/004)
- [x] T5. `tests/test_mfa_acr.py`: unit (amr/acr/malformed/kc26-shape) + API
  (mint, /api/me, JWT stateless, enroll) + regresión `amr`. (AC-MFA-001/002/004/006)
- [x] T6. Converge: suite completa `platform/tests/` en verde (ver conteo en
  spec §6 / evidencia de la corrida 2026-10-24), `TRUST_MFA_MIN_ACR`
  documentado por entorno en `platform/README.md` (D-MFA-04), referencias
  cruzadas en spec 003 (D-CP-07, REQ-CP-022, AC-CP-024) y plan 007 §2,
  verificación en vivo ya asentada (smoke_console.py 21/21, realm real
  KC 26.2). (AC-MFA-003/005)
