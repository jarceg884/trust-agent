# Tasks — Spec 027

- [x] T1 — Comentario hotspot migración en el card (regla 9) + seam del
       contrato en t_c875626f.
- [x] T2 — spec.md (requisitos EARS + contrato §4 + decisiones D-027-01..07).
- [x] T3 — plan.md.
- [x] T4 — Migración 0017_live_policy_presence (columnas devices + audit
       actions; re-colgada del head real 0016 que movieron lanes hermanas)
       + paridad schema.sql (test_schema_parity 2/2) + rebuild imagen
       migrate + up -d (BD live en 0017).
- [x] T5 — Settings (heartbeat_interval_seconds=300,
       presence_online_window_seconds=600, heartbeat_audit_interval_seconds=3600).
- [x] T6 — platform/app/devices/heartbeat.py: POST /api/me/device-heartbeat
       (auth bundle, DeviceGate, persistencia, señal de actualización, audit
       muestreado + policy.propagated) + registro en main.py.
- [x] T7 — GET /api/devices/presence (service + router, dept-scope,
       registrada antes de /{device_id}).
- [x] T8 — Tests backend AC-027-01/02/04/05/07 (TestClient, DB efímera
       canónica) + AC-027-03 (BundleSyncer real contra HTTP real): 10/10
       PASS; regresión: 111/111 en suites relacionadas + db/tests 182/182.
- [x] T9 — Console: franja Live connections + columnas Online/Policy/Last
       heartbeat + poll 15 s (types.ts + devices-page.tsx). Build exit 0
       (20 rutas) + lint 0 problems.
- [x] T10 — Rebuild api + console (--force-recreate) y verificación en vivo:
       smoke dedicado 21/21 PASS (enrollment real → heartbeat → policy →
       propagación → presencia → revoke), heartbeat real contra :8000 con
       presencia+audit en la BD live, rutas 401 sin auth, console sirve el
       bundle nuevo.
- [x] T11 — docs/live-policy-presence.md (ventana de consistencia,
       fail-closed, runbook) + converge: AC-027-01..07 con evidencia real.
