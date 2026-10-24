# Tasks — Spec 006: Vault de credenciales, conectores y mensajerías

- [x] T1 — Spec (`spec.md`) con REQ-VC EARS + ACs y decisiones D-VC vinculantes
- [x] T2 — Plan técnico (`plan.md`)
- [x] T3 — `errors.py`: reasons `connector_not_allowed`, `credential_type_not_allowed`, `messaging_mode_not_allowed`
- [x] T4 — `vault/service.py`: gobernanza (tipos/conectores/scopes), cifrado, visibilidad por rol, rotación, revocación con corte, canal de uso
- [x] T5 — `vault/router.py`: rutas `/api/vault/credentials` con MfaGate
- [x] T6 — `connectors/service.py`: `disconnect_connector_instances()` + gobernanza de mensajería
- [x] T7 — `connectors/router.py`: `/available`, instancias (alta/reactivación/listado/desconexión)
- [x] T8 — `main.py`: registrar router del vault (diff mínimo)
- [x] T9 — `tests/test_vault_connectors.py`: tenant C aislado; ACs VC-001…015 — 14/14 en verde
- [x] T10 — Converge: suite completa 108/108 en verde sobre PostgreSQL 17 real; greps de seguridad limpios (sin `verify=False`, sin prints/secretos en vault/connectors, canario solo en respuestas one-time); arranque live verificado (uvicorn :8100, `/healthz` 200, rutas del vault registradas, 401 con envelope §5.2 sin autenticación)
