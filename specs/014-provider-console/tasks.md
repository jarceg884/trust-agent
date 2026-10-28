# Spec 014 — Tasks

> Formato: `[x]` = done con evidencia. Cada task cita su REQ/AC.
> Evidencia completa en spec.md §5 y docs/provider-console.md.

- [x] T1 — Backend `service.py`: `list_tenants` (migrator para filas + stats como trust_app con scope, D-PC-04/08) y `set_tenant_status` (transaccional + audit + idempotente, D-PC-05). (REQ-PC-001/002/003) — `platform/app/tenants/provider_service.py`.
- [x] T2 — Backend `router.py`: `GET /api/admin/tenants`, `POST /{slug}/suspend`, `POST /{slug}/reactivate` con provider principal + `tenants.provision` (D-PC-03). (REQ-PC-001…004) — `platform/app/tenants/router.py`.
- [x] T3 — Tests `tests/test_provider_admin.py`: matriz 401/403 data-driven, list con seeds, suspend/reactivate + audit + idempotencia + 404, 403 tenant_suspended sobre sesión activa, higiene de secretos. (AC-PC-001…005) — **13 passed** (2026-10-26, antes de la colisión transitoria de spec 019 en la conftest compartida — ver docs/provider-console.md §3).
- [x] T4 — Scaffold `platform/provider-console/` (Next 16.4.3 + TS strict + Tailwind v4) + lib (config, provider-session, control-plane, types, format). (REQ-PC-010, D-PC-01/02)
- [x] T5 — Auth: `/login` (form del token), `/auth/provider/login|logout` (validación real contra el API, cookie httpOnly `ta_provider`). (REQ-PC-010, AC-PC-010) — evidencia §5.7.
- [x] T6 — Proxy BFF `/api/proxy/[...path]` con `Authorization` desde cookie. (D-PC-02)
- [x] T7 — UI kit + shell enterprise (sidebar/topbar/logout, gate de sesión). (REQ-PC-011)
- [x] T8 — Tenants list (tabla real con status/realm/users/budget/spend/created). (REQ-PC-011, AC-PC-011) — evidencia §5.3.
- [x] T9 — Provision form + panel OTA one-time (D-PC-06) con errores reales. (REQ-PC-012, AC-PC-012) — evidencia §5.1/§5.2 (provisioning REAL de globex-industries + idempotencia).
- [x] T10 — Tenant detail: estado, counts, healthcheck en vivo, suspend/reactivate con confirmación, link a Admin Console (D-PC-07). (REQ-PC-013/014, AC-PC-013/014) — evidencia §5.4/§5.5.
- [x] T11 — Dockerfile + servicio compose `provider-console` (:3200) + rebuild/restart del API. (plan §3.1) — también servicio durable `desktop-client` (:9090).
- [x] T12 — Verificación: `npm run build` + `npm run lint` limpios; pytest (nuevos + no-regresión). (AC-PC-015, spec §5) — build/lint 0 problemas; 13/13 provider tests; suite completa rota transitoriamente por spec 019 en vuelo (documentado).
- [x] T13 — Evidencia real: provisioning de tenant REAL desde la UI en navegador + screenshots + OCR + curl + SQL → `docs/provider-console.md` + `docs/evidencia/provider-console/`. (REQ-PC-016, AC-PC-016)
- [x] T14 — Converge: checklist spec §5, desviaciones documentadas. (ver spec.md §5)
- [x] T15 — Hermes download link por tenant (D-PC-09, REQ-PC-017, mandato Miller): prefill `?org=` en el login del desktop-client (extensión spec 010) + acción/link en el detalle del Provider Console. — evidencia §5.6. **[REVOCADO después — corrección Miller t_360a752e (2026-10-26): el link se MUDÓ al Admin Console del cliente (spec 009 REQ-AC-033/D-AC-11); el Provider Console ya no lo muestra. El prefill `?org=` del desktop-client se conserva (extensión spec 010, inocua). Ver docs/evidencia/hermes-download-admin-console/.]**
