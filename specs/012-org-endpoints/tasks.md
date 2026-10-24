# Tasks — Spec 012 Endpoints de organización

- [x] T1. Spec/plan/tasks (este documento).
- [x] T2. Capa de datos: sección 10 en `db/schema.sql` + migración `0005_org_endpoints` (acción `branding.updated`).
- [x] T3. `app/org/directory.py` + `GET /api/users` con scoping por rol (REQ-ORG-100).
- [x] T4. `app/org/invite.py`: enqueue + handler `user.invite` + alias KC (REQ-ORG-101/102). Enqueue comparte la tx del request (D-ORG-02: el advisory lock de la cadena de audit prohíbe una segunda conexión).
- [x] T5. `PUT /api/users/{id}` + `POST /api/users/{id}/disable` (REQ-ORG-103/104).
- [x] T6. `app/org/branding.py` + `GET/PUT /api/branding` (REQ-ORG-110/111).
- [x] T7. `app/org/departments.py` + `POST /api/departments` (REQ-ORG-121).
- [x] T8. `agents.list_grants` + `GET /api/agents/{agent_id}/grants` (REQ-ORG-120).
- [x] T9. Tests `tests/test_org_endpoints.py` (19) + suite existente en verde (217 passed).
- [x] T10. Activación console: smoke_authorized.py 404→200 + build del console limpio + smoke live.
