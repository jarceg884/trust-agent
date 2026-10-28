# Tasks — Spec 028 (branding REQ-BRD-004)

- [x] T1 — Spec delta 028 (spec/plan/tasks) con decisiones D-028-01…07.
- [x] T2 — Comentario de anuncio de migración + hotspot en la card (regla 9).
- [x] T3 — Migración `0018_branding_versioning` vía migration_guard + paridad
      db/schema.sql §16 + BUSINESS_TABLES + audit_actions + seed/inserts de tests db.
- [x] T4 — Backend: draft/publish/rollback/assets en branding.py + router.py
      (+ python-multipart).
- [x] T5 — Tests API nuevos (test_branding_versioning.py, 7 tests) y
      actualización de test_org_endpoints.py al contrato draft.
- [x] T6 — Console: rework /branding (merge del fix @ui t_6d3f5016 + uploads,
      preview, publish, rollback), sidebar con brand del tenant
      (TenantBrand), favicon runtime, types.ts, apiUpload/proxyUrl.
- [x] T7 — Desktop: resolveLogoUrl (asset → data URI con bearer) en el dashboard.
- [x] T8 — Rebuild imagen platform (vía servicio migrate — el api no tiene
      sección build) + console + desktop-client + compose up + migración aplicada.
- [x] T9 — Evidencia real E2E: docs/evidencia/pilar2/branding-req-brd-004/
      (EVIDENCIA.md + 2 capturas). Login real PKCE+TOTP, uploads reales,
      publish v1, console y desktop sirven el logo, sha256 extremo a extremo,
      rollback v1→v3, audit completo.
- [x] T10 — Convergencia: AC-028-1…5 por tests (26 passed platform branding,
      207 passed db), AC-028-6 por evidencia E2E real.
- [x] T11 (t_42e23b01) — Titles dinámicos + refresh live del shell:
      D-028-08/09, REQ-BRD2-007/008. generateMetadata en el layout del
      console (template "%s — {product_name}", fetch cache() compartido
      con el acento), 12 pages con título corto, TenantBrand escucha
      trust:branding-updated + mantiene document.title, /branding emite
      el evento y router.refresh() tras publish/rollback. AC-028-7 por
      evidencia E2E real (docs/evidencia/pilar2/t42e23b01-branding-titles-evidencia.md).
