# Plan — spec 036 (policies legibles, visión Miller)

**Fase**: plan (SDD) · **Autor**: ui (t_f0d2335a) · **Fecha**: 2026-10-28

## Objetivo

Capa de presentación legible sobre el engine EXISTENTE: catálogo legible en el
backend viajando como bloque `presentation` aditivo de `GET /api/policies/matrix`
(D-36-01), panel de tarjetas por categoría como vista por defecto de `/policies`
con asignador "Apply to" (D-36-02/03), frases derivadas del valor EFECTIVO del
backend (D-36-04) y multiselects como sub-toggles por ítem (D-36-08). Engine
intacto (REQ-036-006): cero mutaciones nuevas, cero cambios de semántica en
`combinator.py`/`policies.py`.

## Arquitectura

```
specs/036-policies-ux/policy-catalog-readable.json   (contrato canónico, product-owner)
        │ copia byte-a-byte (sha256 verificado en test)
        ▼
platform/app/governance/policy_catalog_readable.json (package data del backend)
        │ policy_presentation.py (loader read-only, lru_cache)
        ▼
GET /api/policies/matrix  ── + presentation{key→bloque} + categories[9]
                          ── + org_effective (combine(org,∅,∅))
                          ── + departments[].effective (combine(org,dept,∅))
        ▼
console /policies (panel legible por defecto; "Technical view" solo org_admin)
```

- **Frases por destinatario**: scope=user → `GET /api/policies/effective`
  (effective + source por key, ya existente); scope=dept →
  `matrix.departments[].effective` (nuevo, computado con el combinator en el
  backend); scope=org → `matrix.org_effective`. La UI JAMÁS re-deriva (D-36-04).
- **Escritura**: SOLO los PUT/DELETE existentes por nivel (`/api/policies/org`,
  `/api/policies/dept/{id}`, `/api/policies/user/{id}`,
  `DELETE /api/policies/{level}/{key}`) — el asignador "Apply to" elige el nivel
  y la tarjeta escribe en ese nivel (D-36-03).
- **Ítems dinámicos** (D-36-05/08): `models` → `GET /api/models`;
  `connector_catalog` → `GET /api/connectors`; `connector_catalog_messaging` →
  ídem filtrado por `category=messaging`; `connections` →
  `GET /api/vault/connections`. Slugs sin etiqueta conocida se muestran tal cual.

## Componentes

| # | Componente | Archivos |
|---|-----------|----------|
| 1 | Contrato en plataforma + loader | `platform/app/governance/policy_catalog_readable.json` (copia), `platform/app/governance/policy_presentation.py` (nuevo) |
| 2 | matrix aditivo | `platform/app/governance/policies_matrix.py` (+`presentation`, `categories`, `org_effective`, `departments[].effective`) |
| 3 | Tests de cobertura 1:1 + sha256 | `platform/tests/test_policies_presentation.py` (nuevo) |
| 4 | Tipos frontend | `console/src/lib/types.ts` (+`PolicyPresentation`, `PolicyCategory`, extensiones de matrix) |
| 5 | Iconos lucide + script de verificación | `console/src/app/(console)/policies/readable/policy-icons.tsx`, `console/scripts/check_policy_icons.mjs` |
| 6 | Helpers de presentación (frases, fallback, labels) | `.../readable/presentation.ts` |
| 7 | Controles legibles (toggle/select/number/multiselect sub-toggles/domains/messaging/locked) | `.../readable/policy-control.tsx` |
| 8 | Tarjeta legible + 422 inline | `.../readable/policy-card.tsx` |
| 9 | Panel por categorías + buscador + asignador + "Provider protections" | `.../readable-policies-panel.tsx` |
| 10 | Vista técnica (spec 033 intacta, solo org_admin) | `.../technical-view.tsx` (extracción del código actual) |
| 11 | Página /policies (conmutador de vistas) | `.../policies-page.tsx` (reescritura) |
| 12 | Evidencia E2E + converge | `docs/evidencia/policies-ux/evidencia-e2e.md`, `specs/036-policies-ux/spec.md` §8 |

## Decisiones de implementación (dentro del marco D-36-01..09)

- **Asignador global**: una barra "Apply to: Organization / Department / User"
  fija el nivel de escritura y el destinatario cuya efectiva se muestra; las
  tarjetas de keys `org_only` muestran "Organization-wide" y escriben SOLO a org
  (el asignador no ofrece dept/user para esas keys — D-36-03); las invariables
  van a la sección final "Provider protections" con candado y sin control
  (D-36-09).
- **Forma del bloque presentation**: objeto top-level `presentation` del
  response de matrix, keyed por policy key (facilita el test de cobertura 1:1
  contra `catalog.policy_keys`), + `categories` (9). Aditivo: clientes viejos
  (spec 033) no se rompen.
- **Commit de controles**: toggles/selects/sub-toggles → PUT inmediato con
  estado saving; number/domains → input + botón "Apply" (evita PUTs por
  keystroke). Tras éxito: refetch real; tras 422: mensaje real inline +
  explicación en palabras, control vuelve al valor vigente (REQ-036-004).
- **Locale**: el contrato viaja es+en completo; el console renderiza `en`
  (constitution VI) vía helper `t()` — cambiar de locale es data-only (D-36-07).
- **Fallback** (D-36-05): key sin `presentation` → control genérico por
  `value_type`, nombre = key capitalizada, badge "Untranslated". Hoy imposible
  (test de cobertura), forward-compatible.

## Riesgos

- **Tamaño del panel** (47 tarjetas): buscador + agrupación por 9 categorías
  colapsables por defecto? No: tarjetas compactas (una línea de control), las
  categorías como secciones; el buscador filtra client-side.
- **Drift del contrato**: test de cobertura 1:1 + sha256 (AC-036-001) — falla en
  CI si falta/sobra una key o el JSON de plataforma diverge de la spec.
- **`GET /api/vault/connections` exige MFA**: la sesión del console admin es MFA
  (login PKCE+TOTP); si falla, los ítems dinámicos de `allowed_connections`
  degradan a slugs tal cual (estado de error no bloqueante, no simulado).
