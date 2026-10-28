# Tasks — Spec 026

- [x] T1 — Spec + plan (este documento).
- [x] T2 — Anuncio de migración en la card (regla 9) con down_revision = head.
- [x] T3 — Migración 0015 + paridad schema.sql (CHECK pending_activation + 3 audit actions). Paridad 2/2 PASS; BD live en head 0017 (0015 aplicada).
- [x] T4 — errors.py (423 tenant_pending_activation) + config.py (self_signup_enabled).
- [x] T5 — auth/service.py: gate 423/403 + get_principal_allow_pending; logout lo usa.
- [x] T6 — provision.py parameterizado + tenants/self_signup.py + POST /api/auth/signup-org (rate-limited).
- [x] T7 — provider_service.activate_tenant + router activate + filtro status.
- [x] T8 — Console: /signup + PendingActivationGate + layout hook + links del seam.
- [x] T9 — Provider console: badge/filtro pending + panel Activate con budget. Bundle servido verificado; click-through UI pendiente del credential de operador de la lane 025 (comentario en t_6c2be725) — el flujo API que conduce está probado en vivo.
- [x] T10 — Tests test_tenant_self_signup.py (AC-26-001..006) en verde — 9/9 (+44 de regresión provider_admin/provision/auth_ux).
- [x] T11 — Migración aplicada + imágenes rebuild (migrate/api/console/provider-console sirven el código — endpoints y UI verificados en los contenedores vivos).
- [x] T12 — Evidencia viva del journey (AC-26-007/008/009) + docs/evidencia — 26/26 PASS (docs/evidencia/tenant-self-signup/).
- [x] T13 — Convergencia: tasks.md actualizado, resumen kanban.
