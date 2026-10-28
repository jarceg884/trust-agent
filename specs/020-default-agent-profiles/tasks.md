# Tasks — Spec 020

- [x] T1 — Spec 020 (spec.md/plan.md/tasks.md) en specs/020-default-agent-profiles/.
- [x] T2 — Migración 0009_default_agent_profiles + espejo en db/schema.sql (columna profile_key, índice único parcial, seed audit action). Aplicada en la DB live (merge 12c08d4fb858 con las ramas 0009 paralelas).
- [x] T3 — app/governance/agent_profiles.py (catálogo analyst/coder/ops/support + list_profiles/adopt) + errors.budget_cutoff.
- [x] T4 — invoke_gate con model_name + budget cutoff; router: GET/POST /api/agent-profiles, adopt, InvokeBody.
- [x] T5 — tests/test_agent_profiles.py (AC-020-01…06): 15/15 PASS (test_agent_profiles + test_profiles_templates + test_agents) con DB aislada TRUST_TEST_DB.
- [x] T6 — Console: types + sección Default profiles (adopt/effective policy/invoke) + build en verde (contenedor vivo verificado en el E2E).
- [x] T7 — Evidencia real: login PKCE+TOTP real por navegador (tenant profiles-e2e-020), adopt de Analyst desde la UI, invoke glm-5.2 → 200, gpt-4o → 403 model_not_approved, audit agent.profile_adopted/agent.invoked/model.access_denied verificado (docs/evidencia/default-agent-profiles/).
- [x] T8 — Converge: implementación == spec, AC-020-01…07 con evidencia, reporte a Orchestrator.
