# Tasks — Spec 015 Auth UX

- [x] T1. Spec/plan/tasks (este documento) + skill `identity/keycloak26-auth-ux` (investigación previa — hecho).
- [x] T2. **Probe en vivo** `scripts/probe_loa_flow.py` contra KC 26.2 (semántica D-AUTH-06: acr1 sin OTP, acr2 con enrolamiento inline, SSO re-login). D-AUTH-06 congelada (hallazgos S1–S8/G1/B1 en docs/evidencia/auth-ux/probe-loa-findings.txt) + skill actualizado.
- [x] T3. Migración `0014_auth_ux_policies` (require_mfa variable/default false; allow_self_registration; audit actions). Tests db/ stale corregidos (182 passed).
- [x] T4. `keycloak_admin.py`: `ensure_login_theme`, `ensure_google_idp` (fail-closed), `ensure_tenant_id_hardcode_mapper`, rework del flujo LoA (D-AUTH-06), required actions parametrizados. Dos bugs de idempotencia corregidos con evidencia: DELETE nested-inclusive (regresión realms nuevos) y colisión de alias de config LoA en realms spec-011 (retro 62/62).
- [x] T5. `provision.py` + retro `scripts/apply_auth_ux_realms.py`: 62 realms actualizados, 0 fallos (idempotente, converge desde cableado spec 011).
- [x] T6. `auth/register.py` + `POST /api/auth/register` + `GET /api/auth/org-config` (rate-limited).
- [x] T7. Mint con auto-provisión brokered (D-AUTH-05) + `require_mfa` policy-aware (D-AUTH-07).
- [x] T8. Tema `trust-agent` (theme.properties + CSS sobre keycloak.v2, marca vía displayNameHtml) + volume docker-compose + binding realm.
- [x] T9. Console: org-config → acr condicional, registro (página + link), banner MFA persistente + Set up MFA, callback sin step-up forzado cuando policy false. Estándar UI pro aplicado (lucide, ta-*, active:scale).
- [x] T10. Desktop: acr por org-config (paridad, mismo contrato).
- [x] T11. Tests: `test_auth_ux.py` (unit) + e2e actualizado (`acr_values` explícitos, gate policy-aware, require_mfa org-tunable) — e2e 10/10; suite platform 478 passed (2 fallos restantes son de lanes hermanas, ver EVIDENCIA.md §AC-AUTH-008).
- [x] T12. Evidencia live con navegador real (AC-AUTH-001..005, 007) + screenshots — docs/evidencia/auth-ux/EVIDENCIA.md + screenshots/.
- [ ] T13. Google punta a punta (AC-AUTH-006) — **DESCOPED del v0.1.0-pilot por enmienda spec v1.2.0 §7 (decisión por default 2026-10-27, t_f372f47f): pass-with-documented-gap para el gate; re-entra en v0.1.1 con las credenciales del operador (client ID/secret de Google Cloud Console, redirect URI por realm `http://127.0.0.1:8080/realms/<org>/broker/google/endpoint`). Mecánica de brokering + mapper hardcode + auto-provisión implementadas y probeadas contra doble declarado; fail-closed re-verificado en vivo 2026-10-27.**
- [x] T14. Converge: checklist spec §5 en EVIDENCIA.md, desviación documentada (AC-AUTH-006), skill actualizado con los pitfalls de la implementación.
- [x] T15. (v1.1.0, GAP-10.2 / t_edbcface) D-AUTH-11: sync lazy de `users.mfa_enrolled` en los funnels de auth con claims MFA verificados (mint, JWT stateless, devices/enroll) + exposición en directorio (`GET /api/users`, `PUT /api/users/{id}`) y badge MFA en la página Users del console. Tests unit + evidencia live (AC-AUTH-010).
