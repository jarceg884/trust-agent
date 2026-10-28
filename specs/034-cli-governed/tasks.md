# Tasks — Spec 034 (cli-governed)

- [x] T1. Migración Alembic `device_type` (migration_guard, paridad schema.sql,
      rebuild imagen migrate + compose up).
- [x] T2. `devices` service+router: `device_type` en enroll (validación 422),
      persistencia y exposición (list/detail/me/presence).
- [x] T3. Bundle: miembro `skin.yaml` (D-034-02).
- [x] T4. `download/cli.py`: builder del instalador CLI por tenant.
- [x] T5. `download/router.py`: `GET /download/{slug}/cli` (throttle,
      anti-enumeración, fail-closed, audit artifact=cli) + api_base a la página.
- [x] T6. `download/page.py`: página con Desktop + CLI (comando curl copiable,
      instrucciones Linux/macOS/WSL2, branding).
- [x] T7. Plugin `enroll.py`: enviar `device_type` (env TRUST_DEVICE_TYPE).
- [x] T8. Console: types + Hermes Download (comando CLI copiable) + Devices
      (badge de tipo).
- [x] T9. Tests `test_cli_governed.py` (endpoint, enroll device_type, bundle
      skin.yaml, exposición en APIs) — 10/10 verde; vecinos
      (hermes_download, devices_flow, presence, governance, heartbeat,
      attestation) re-verificados; paridad schema 2/2.
- [x] T10. E2E live `evidence_cli_governed.py` — 51 PASS / 0 FAIL contra el
      stack real (curl|bash en home limpio → pending cli → approve → modelo
      no aprobado bloqueado 403 → revoke → 401). Evidencia en
      `docs/evidencia/cli-governed/`.
- [x] T11. Docs `docs/cli-governed.md` + README de evidencia (español).
