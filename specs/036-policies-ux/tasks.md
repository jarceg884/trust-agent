# Tasks — spec 036 (policies legibles, visión Miller)

**Fase**: tasks (SDD) · Derivadas de plan.md. Checklist de implementación.

## Backend (read-only, engine intacto)

- [x] T1. Copiar byte-a-byte `specs/036-policies-ux/policy-catalog-readable.json`
      → `platform/app/governance/policy_catalog_readable.json` (verificar sha256).
- [x] T2. `policy_presentation.py`: loader read-only (lru_cache) del JSON —
      `presentation_blocks()`, `presentation_categories()`. Cero cambios a
      `combinator.py`/`policies.py`.
- [x] T3. `policies_matrix.py` aditivo: `presentation` + `categories` +
      `org_effective` (combine(org,∅,∅)) + `departments[].effective`
      (combine(org,dept,∅)) en matrix.
- [x] T4. `tests/test_policies_presentation.py`: cobertura 1:1 presentation ↔
      catalog.policy_keys (47/47 sin faltantes/sobrantes), sha256 plataforma ==
      contrato de spec, org_only/invariable del contrato == DB, campos
      obligatorios por kind, org_effective/dept effective == combinator,
      dept_admin también recibe presentation. (+ tolerancia a key sin
      presentation, REQ-036-007.)

## Frontend (Admin Console)

- [x] T5. `types.ts`: `LocalizedText`, `PolicyPresentation`, `PolicyCategory`,
      `PolicyControl` (+kinds), extensiones de `PolicyMatrix`/`PolicyMatrixDepartment`.
- [x] T6. `policy-icons.tsx`: mapa explícito de los 47+9 iconos lucide-react +
      fallback al icono de categoría; `check_policy_icons.mjs` verifica
      existencia contra el paquete instalado (AC-036-007) — 49/49 OK.
- [x] T7. `presentation.ts`: `t()` (locale en), `buildStatement()` (frases desde
      valor EFECTIVO con statement/statement_template + labels), fallback
      genérico por value_type marcado "Untranslated", etiquetas de ítems
      (known + dinámicos + slug tal cual), singular de unidades.
- [x] T8. `policy-control.tsx`: toggle (role=switch), select con labels en
      palabras, number+unidad, multiselect como sub-toggles por ítem ✓/✗ con
      wildcard/ítems dinámicos/ítems custom, domains (modo + dominios),
      messaging (por plataforma: allowed + mode), locked sin control.
- [x] T9. `policy-card.tsx`: icono, nombre, descripción, frase efectiva, badge
      de origen, control, estado saving, 422 inline con mensaje real +
      explicación en palabras (REQ-036-004), candado para invariables.
- [x] T10. `readable-policies-panel.tsx`: buscador es+en, 9 categorías,
      asignador "Apply to" (org/dept/user; org_only → solo Organization),
      sección final "Provider protections" (D-36-09), estados reales
      loading/403/error. (+ remount por scope/revision para que el control
      vuelva al valor vigente tras 422 o cambio de destinatario.)
- [x] T11. `technical-view.tsx`: extracción 1:1 de la tabla maestra + diff por
      usuario (spec 033, sin cambios funcionales).
- [x] T12. `policies-page.tsx`: vista por defecto = panel legible; conmutador
      "Technical view" SOLO org_admin (D-36-02); dept_admin sin conmutador.

## Verificación (converge)

- [x] T13. Suite backend: `pytest tests/test_policies_presentation.py
      tests/test_policies_matrix.py tests/test_governance*.py` verde
      (6+4 passed; cadena E2E 10/10).
- [x] T14. `npm run build` exit 0 + lint de policies limpio + script de
      iconos OK (49/49).
- [x] T15. E2E real (login PKCE+TOTP, Chromium vía Playwright):
      `scripts/e2e_policies_ux.py` (API, 13 PASS) +
      `scripts/e2e_policies_ux_api_checks.py` (4/4) +
      `scripts/e2e_policies_ux_shots.mjs` (16/16 checks DOM) — AC-036-002..005.
- [x] T16. Evidencia en `docs/evidencia/policies-ux/evidencia-e2e.md` +
      actualización de §8 (convergencia) de la spec.
