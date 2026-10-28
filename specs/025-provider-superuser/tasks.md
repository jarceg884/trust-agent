# Tasks — Spec 025 (super-user del Provider Console)

- [x] T1 — Spec/plan (este documento) + anuncio de migración en la card.
- [x] T2 — Migración `0016_provider_superuser` + paridad `schema.sql`
      (tablas `ops.provider_*`, triggers chain/inmutabilidad, acciones
      catálogo) + `alembic upgrade head` en BD de test y live (0017 encima).
- [x] T3 — Backend: `app/provider/` (totp, passwords, audit, operator,
      router) + config + errors + `get_provider_principal` + lifespan
      bootstrap.
- [x] T4 — Tests `test_provider_superuser.py` (AC-PS-001…012, 25 tests)
      + suite db/tests (paridad) en verde.
- [x] T5 — Frontend Provider Console (login user+pass+TOTP, change-password,
      Security/MFA con banner, shell) + build/lint limpios.
- [x] T6 — Reconstruir imágenes (api, provider-console, migrate), migración
      live, verificación en vivo completa (OTA del log, curl, reinicio del
      API con sesión sobreviviente, SQL de audit/throttle, inmutabilidad,
      BFF /security) + `docs/provider-superuser.md`.
- [x] T7 — Convergencia: checklist de spec marcado, README + .env.example +
      docs/provider-console.md actualizados.
