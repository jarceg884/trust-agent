# Tasks — Spec 023 (t_526030f3)

- [x] T1 — Spec 023 (spec.md/plan.md/tasks.md) con decisiones D-023-01..13 y contrato con la UI.
- [x] T2 — Anuncio de migración en la card + `migration_guard.py new --slug hermes_download` + contenido (tenants + catálogos) + paridad schema.sql §17.
- [x] T3 — `throttle.register_hit` + settings (rate limits, hermes_plugin_dir, redirect URI del CLI).
- [x] T4 — `app/download/` (router público + admin, bundle.py, page.py) + wiring en main.py.
- [x] T5 — Dockerfile COPY del plugin; build migrate + api; compose con redirect URI del CLI.
- [x] T6 — `platform/tests/test_hermes_download.py` 9/9 (AC-HDL-010, contratos, rate limit, aislamiento); suite completa 512 passed.
- [x] T7 — E2E live: setup tenant real → navegador anónimo descarga → install en home fresco → trust-login (navegador real SSO+MFA) → auto-enroll zero-UI → approve → turno gobernado (AC-HDL-001..009).
- [x] T8 — docs/hermes-download.md + evidencia cruda en docs/evidencia/hermes-download/ (screenshot, HTML, logs, panes).
- [x] T9 — Convergencia: checklist AC contra evidencia (docs/hermes-download.md §2); imágenes reconstruidas; hallazgo del plugin reportado como tarea de follow-up.
