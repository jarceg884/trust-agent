# Tasks — Spec 022 (hermes-real-bridge)

- [x] T1. Leer spec 019 + estado de t_f99e12ab; declarar hotspot y contrato de archivos (comentario en ambas tarjetas).
- [x] T2. Investigar APIs reales del Hermes instalado (pre_tool_call/pre_llm_call/register_command/discovery) y del control plane (bundle.py, devices, tokens) — verificado contra código.
- [x] T3. Spec 022 (spec.md + plan.md + tasks.md).
- [x] T4. Implementar `enroll.py` (keypair, /trust-enroll, token store).
- [x] T5. Implementar `enforcement.py` (verificador, GovernanceState, hooks, veto-counts).
- [x] T6. Tests unitarios AC-HRB-003/004/005 (pytest, sin red) — 22/22 PASS (2026-10-26).
- [x] T6b. Heartbeat spec 027 §4 en el plugin (D-HRB-09): piggyback en BundleSyncer,
      re-baja en el mismo ciclo, 401→revoked, best-effort en red, autoajuste D-027-02.
      Tests AC-HRB-009(a): tests/test_hrb_heartbeat.py — 15 nuevos, 37/37 PASS (2026-10-26).
- [x] T7. Infra live: clave JWS en .env, rebuild imagen, restart api, JWKS vivo.
- [x] T8. Tenant de prueba + policy allowed_tools + virtual key.
- [x] T9. Perfil Hermes real trust-e2e + plugin instalado + config.yaml.
- [x] T10. Cableado final en __init__.py (tras/coord con Inc. 1) + AC-HRB-006.
      (Hallazgo crítico: gateway.standalone:true — sin gateway dedicado del
      perfil, el turno corre contra el plugin manager del scope default del
      gateway host y el veto no dispara. Fix del handler governance_status:
      args posicional. Ver docs/hermes-real-bridge.md §0.1/§0.2.)
- [x] T11. Enrolamiento en vivo (AC-HRB-001) + enforcement en vivo (AC-HRB-003/004c)
      + heartbeat en vivo (AC-HRB-009b: presencia en DB + policy en vivo sin reinicio).
      (Demos reales 2026-10-26 17:00-17:45 CST: /trust-enroll + auto-enroll zero-UI,
      veto terminal/web_search, policy v1→v2→v3 en el mismo ciclo de heartbeat,
      revocación → restricted fail-closed. Transcript + plugin.log en
      docs/evidencia/hermes-real-bridge/.)
- [x] T12. Suite de Hermes con plugin presente (AC-HRB-007).
      (Runner canónico; slice maquinaria de plugins 44/44 PASS; slice
      hooks/sesión 14 pass + 2 fail de infraestructura del test (skills
      seeding, ajenos al plugin — aislados y atribuidos). Runner hermético:
      plugin no carga en la suite por construcción. git del core limpio.)
- [x] T13. Evidencia: docs/hermes-real-bridge.md (AC-HRB-008) + convergencia marcada.
