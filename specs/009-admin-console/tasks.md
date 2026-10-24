# Spec 009 — Tasks

> Formato: `[x]` = done con evidencia. Cada task cita su REQ/AC.
> Evidencia completa en spec.md §5 (checklist de convergencia).

- [x] T1 — Scaffold `platform/console/` (Next 16.4.3 + React 19.3 + TS strict + Tailwind v4, paridad desktop-client) + configs + `npm install`. (plan §2)
- [x] T2 — `lib/`: config, oidc (PKCE S256 + acr_values D-MFA-01), session (cookies httpOnly + stepup), control-plane (server), types (derivados, citan fuente), format (USD), api-client (proxy). (REQ-AC-001/003, D-AC-01…03/08)
- [x] T3 — Auth: `/login`, `/auth/login`, `/auth/callback` (mint kind=admin vía Set-Cookie + step-up MFA), `/auth/logout`. (REQ-AC-001/004, AC-AC-001/004)
- [x] T4 — Proxy BFF `/api/proxy/[...path]`: cookie + CSRF double-submit estricto + refresh-on-401 único. (REQ-AC-003, AC-AC-003)
- [x] T5 — UI kit (button/card/badge/table/input/select/dialog/skeleton/empty/error/toast/stat/progress/pagination/confirm/one-time-secret/copy) + shell (sidebar GOVERNANCE, topbar, notificaciones reales, user-menu, mfa-gate). (D-AC-04, REQ-AC-002/031/032)
- [x] T6 — Overview. (REQ-AC-010, AC-AC-010)
- [x] T7 — Budgets. (REQ-AC-014)
- [x] T8 — Organization. (REQ-AC-011)
- [x] T9 — Departments. (REQ-AC-012)
- [x] T10 — Model Access. (REQ-AC-015)
- [x] T11 — Devices. (REQ-AC-017)
- [x] T12 — Credentials (+ conectores). (REQ-AC-018)
- [x] T13 — Shared Agents. (REQ-AC-016)
- [x] T14 — Audit Log (filtros/paginación/export/verify). (REQ-AC-019)
- [x] T15 — Users & Roles con degradación honesta. (REQ-AC-013, D-AC-06)
- [x] T16 — Branding con degradación honesta + branding runtime del shell. (REQ-AC-020, D-AC-07)
- [x] T17 — `npm run build` limpio (18 rutas, exit 0) + `npm run lint` (0 errors). (REQ-AC-030, AC-AC-030)
- [x] T18 — Smoke real: `smoke_console.py` PASS 21/21 (login REAL Keycloak 26: PKCE + OTA + TOTP + step-up MFA acr + sesión MFA-verificada + datos reales + CSRF) y `smoke_authorized.py` PASS 32/32 (camino autorizado completo + 11 páginas + logout). (AC-AC-001/002/003/010/030)
- [x] T19 — Converge: checklist spec §5 con evidencia, desviaciones documentadas (step-up D-MFA-01, puerto 3100), tareas dev t_59afbe56 (bug amr) y t_f72dc691 (contratos pendientes), skill keycloak-admin-oidc pitfalls #10-#12.
