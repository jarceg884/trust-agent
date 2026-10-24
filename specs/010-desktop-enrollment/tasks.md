# Tasks — Spec 010 (Desktop: flujo de enrolamiento white-label)

| Campo | Valor |
|---|---|
| **Spec** | specs/010-desktop-enrollment/spec.md |
| **Plan** | specs/010-desktop-enrollment/plan.md |

- [x] 1. Investigación: contrato API 007/003 (routers reales), mockup OCR + paleta, mecanismos plugin Hermes verificados en `hermes_cli/plugins.py`, redirect URIs/cliente público de `config.py`.
- [x] 2. Fase specify: spec.md con REQ-DTB-001…007 (EARS) + decisiones D-DTB-01…09.
- [x] 3. Fase plan: plan.md (arquitectura server-side, estructura, riesgos).
- [x] 4. Scaffold `platform/desktop-client/` (Next.js 16 App Router + TS + Tailwind 4) y lib base (config, control-plane con envelope §5.2, session cookies httpOnly, oidc PKCE S256).
- [x] 5. Login: pantalla org code + server action PKCE + route handler callback (canje + mint sesión + cookies httpOnly). **AC-DTB-001** — verificado en vivo contra Keycloak real (redirect PKCE, required actions TOTP+password, callback, mint 201).
- [x] 6. Dashboard RSC: identidad, presupuesto (cutoff), políticas, modelos aprobados, dispositivos, notificaciones — todo real, estados loading/error/empty. **AC-DTB-004, AC-DTB-007** — verificado en vivo (datos reales del tenant demo-org).
- [x] 7. Enrolamiento: WebCrypto keypair + fingerprint, route handlers enroll/exchange, polling 202→200→410, keystore local. **AC-DTB-002, AC-DTB-003** — verificado en vivo (202 pending → approve → canje one-time → device token local → active).
- [x] 8. Artefactos Hermes oficiales: `hermes/profile-template/config.yaml` + `hermes/plugins/trust-agent-governance/` (plugin.yaml + register(ctx)) + README del cableado. **AC-DTB-005** — hallazgo incorporado: `pre_llm_call` es hook de contexto (no veto); veto de tools = `pre_tool_call`; gate de modelos = LiteLLM (D-GOV-15).
- [x] 9. `docs/desktop-flow.md`: flujo completo documentado (login → enrolamiento → aprobación → políticas/perfiles/skills → sesión gobernada + integración Hermes).
- [x] 10. `npm run build` limpio. **AC-DTB-006** — exit 0, TypeScript OK.
- [x] 11. Smoke en vivo contra API + Keycloak reales (browser): login PKCE+MFA → enroll → approve → device token → dashboard real. **AC-DTB-001…004** — verificado (ver checklist §5 de la spec). Hallazgo KC26/amr → tarea dev creada.
- [x] 12. Converge: checklist §5 de la spec cerrado con evidencia.
