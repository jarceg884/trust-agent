# Spec 036 — Policies legibles en lenguaje natural: panel de políticas asignables a org/depto/usuario (visión Miller)

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* (SDD) — catálogo legible definido y validado; implementación encargada a ui/dev |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-28 |
| **Autor** | product-owner (tarea t_2478addc) |
| **Padre** | specs/033-policies-unified-console (página /policies y endpoints matrix/effective), specs/029-policy-catalog-v2 (catálogo tipado 29 keys), specs/005-governance (combinator deny-overrides, REQ-POL-003/004/007), specs/009-admin-console |
| **Nota de numeración** | La card decía "spec 032-policies-ux" pero 032 está tomada (dynamic-model-discovery); esta spec es la **036** (siguiente número libre). |
| **Idioma** | Español (docs) / bilingüe es+en (catálogo legible) / inglés (código) |

---

## 1. Problema (repro de Miller)

> "En policies me parece que esto es muy poco user friendly, no es legible para un
> usuario normal. […] Yo me lo imagino como un panel de políticas que existen y yo
> las asigno a un departamento o usuario o organización."

Hoy la página `/policies` (spec 033) muestra la **tabla maestra técnica**: una fila
por key cruda (`allowed_connectors`, `require_mfa`…) con valores JSON. Es correcta
para ingenieros, ilegible para un admin de negocio. Miller quiere **frases**:
"Este usuario jamás podrá buscar en internet", "Debe usar MFA", activables y
**asignables** a organización / departamento / usuario.

Esta spec añade la **capa de presentación legible** sobre el engine EXISTENTE:
catálogo legible (47 keys → nombres, descripciones, controles y frases bilingües),
panel tipo "app store" con asignador de alcance, y vista por destinatario. El
combinator deny-overrides, la validación 422 y la no-relajación (REQ-POL-007)
**no cambian**.

## 2. Alcance

**Dentro de alcance**:

1. **Catálogo legible canónico** (este documento lo define COMPLETO): el contrato
   `policy-catalog-readable.json` (junto a esta spec) mapea las 47 keys de
   `catalog.policy_keys` a: nombre humano es/en, descripción es/en, control
   legible (toggle/select/number/multiselect/domains/messaging/locked) con
   opciones en palabras ("Permitir / Bloquear / Solo estos sitios…"), icono
   lucide-react, categoría (9), y plantillas de frase para la vista por
   destinatario. **Validado contra el estado real**: cobertura 1:1 con las 47
   keys de la DB viva, `org_only` == `combinator.ORG_ONLY_KEYS`, `invariable` ==
   DB, enums == `STRING_RANKS`/`WEB_DOMAIN_MODES`/`MODE_RANKS`.
2. **Backend (dev)**: exponer el catálogo legible en `GET /api/policies/matrix`
   como bloque aditivo `presentation` por key (cero mutaciones nuevas, cero
   cambios de semántica).
3. **Frontend (ui)**: rediseño de `/policies` — panel de tarjetas legibles
   (vista por defecto) + asignador "Aplicar a" + vista por destinatario; la
   tabla maestra de spec 033 queda como "Vista técnica" conmutable.
4. **Evidencia real** E2E (login PKCE+TOTP, asignación real a depto/usuario,
   422 de no-relajación mostrado legible).

**Fuera de alcance**:

- Cualquier cambio al combinator, la validación de upsert, el audit o el bundle
  JWS (specs 005/029/019 intactas).
- Nuevas policy keys (el catálogo tipado sigue siendo el de la DB; si nace una
  key, se añade su entrada de presentación — ver D-36-05).
- Edición masiva/bulk y historial de versiones en la página.

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Razón |
|----|----------|-------|
| **D-36-01** | **El catálogo legible vive en el BACKEND como paquete de datos** (`platform/app/governance/policy_catalog_readable.json`, copia byte-a-byte del contrato de esta spec) y viaja al frontend SOLO dentro del bloque `presentation` de `GET /api/policies/matrix`. La UI JAMÁS hardcodea nombres/frases de policies. Un test de plataforma exige cobertura 1:1 `presentation` ↔ `catalog.policy_keys` (falla si falta o sobra una key). | Fuente única de verdad: si la UI duplicara el catálogo, cada key nueva desincronizaría silenciosamente (constitution I). El test de cobertura convierte el drift en fallo de CI. |
| **D-36-02** | **Vista por defecto = panel legible; la tabla maestra spec 033 NO se borra**: queda como "Vista técnica" conmutable (solo `org_admin`). El admin de negocio nunca ve keys crudas salvo que pida expresamente la vista técnica. | Miller pide legibilidad, no amputar la herramienta de depuración que ya converge (spec 033). Coste cero: ya existe. |
| **D-36-03** | **El asignador "Aplicar a" es un selector de alcance, no un endpoint nuevo**: elegir Organización / Departamento / Usuario y ajustar el valor llama a los **PUT/DELETE existentes** de ese nivel (`REQ-GOV-021…023`), con sus permisos, su 422 y su audit. Keys `org_only` (`require_mfa`, `allow_self_registration`) solo ofrecen "Organización" en el asignador (espejo de `ORG_ONLY_KEYS`); keys `invariable` se muestran con candado, sin control. | Cero mutaciones nuevas = cero superficie de bypass. La validación y la no-relajación siguen siendo del control plane. |
| **D-36-04** | **Las frases de la vista por destinatario se derivan del valor EFECTIVO que computa el backend** (`GET /api/policies/effective` para usuarios; `matrix` + combinación por el backend para deptos), nunca re-derivadas client-side. La plantilla (`statement`/`statement_template` + labels de opciones/ítems) viene de `presentation`; el valor con que se rellena `{value}` viene del backend. | La UI no re-implementa deny-overrides (mismo argumento que D-33-01). |
| **D-36-05** | **Fallback genérico forward-compatible**: una key del catálogo sin `presentation` (imposible hoy por el test de cobertura, posible en un despliegue con catálogo extendido) se renderiza con el control genérico por `value_type` y el nombre capitalizado de la key, y se marca visualmente "sin traducir". El test de cobertura la detecta en CI. | El catálogo es extensible por migración (spec 029); la UI no debe romperse ante una key nueva. |
| **D-36-06** | **Iconos = nombres lucide-react** declarados en el contrato (47 keys + 9 categorías). Si un icono no existe en la versión instalada, fallback al icono de la categoría. | El console ya usa lucide-react (t_7cd4f40e); cero SVG caseros. |
| **D-36-07** | **Bilingüe por diseño, render por locale**: el contrato lleva es+en completos; el console renderiza según su locale vigente (hoy inglés, constitution VI). Un futuro cambio de locale es data-only. | Miller habla español; el producto es white-label multi-idioma. |
| **D-36-08** | **El ejemplo de Miller es un ítem, no una key**: "jamás podrá buscar en internet" = desmarcar el ítem `web_search` del control multiselect de `allowed_tools`. Los multiselects muestran sus ítems como sub-toggles legibles con estado por ítem (✓/✗), no como una caja JSON. | Es exactamente la metáfora que Miller describió; generaliza a todas las listas. |
| **D-36-09** | **Invariables = sección "Protecciones del proveedor"**: las 3 keys invariables (`approved_models_only`, `explicit_shared_agent_grants`, `scoped_credentials`) se agrupan al final del panel, con candado, descripción de por qué son fijas, sin control. | Son piso del proveedor (REQ-POL-004); ocultarlas sería opaco, editarlas imposible. |

## 4. Requisitos (EARS)

Trazabilidad: serie **REQ-036**. Padre: specs 005/029/033.

**REQ-036-001** (M) [D-36-01] (catálogo legible canónico) — Cuando el control plane
sirve `GET /api/policies/matrix`, el sistema DEBE incluir por cada key del catálogo
un bloque `presentation` con `name` y `description` (es+en), `icon`, `category`,
`control` (kind + opciones legibles + ítems conocidos + unit/min + wildcard según
tipo) y plantillas de frase (`statement`/`statement_template`), conforme al
contrato `specs/036-policies-ux/policy-catalog-readable.json`; la cobertura
DEBE ser 1:1 con `catalog.policy_keys`.
> **AC-036-001**: (API+test) test de plataforma que compara las keys de
> `presentation` con `SELECT key FROM catalog.policy_keys` → 47/47 sin faltantes
> ni sobrantes; `org_admin` ve `presentation` en matrix; el JSON de plataforma es
> idéntico (sha256) al contrato de la spec.

**REQ-036-002** (M) [D-36-02] (panel tipo app store) — Cuando un admin con
`policies.org.manage` o `policies.dept.manage` navega a `/policies`, la vista por
defecto DEBE ser el panel de tarjetas legibles agrupadas por las 9 categorías del
contrato (icono + nombre humano + descripción + estado efectivo del alcance
seleccionado + control legible), con buscador por nombre/descripción (es+en) y
SIN keys crudas; la tabla maestra de spec 033 DEBE permanecer accesible como
"Vista técnica" solo para `org_admin`.
> **AC-036-002**: (E2E) la vista por defecto no renderiza ninguna key cruda
> (grep del DOM: 0 apariciones de los 47 slugs como texto visible); las 9
> categorías aparecen con sus tarjetas; el buscador filtra por nombre legible;
> org_admin conmuta a "Vista técnica" y ve la tabla spec 033; dept_admin NO ve
> el conmutador.

**REQ-036-003** (M) [D-36-03] (asignador "Aplicar a") — Cuando el admin ajusta una
política desde su tarjeta, la UI DEBE ofrecer el asignador "Aplicar a" con las
opciones Organización / Departamento (los suyos) / Usuario (de su alcance), y
DEBE escribir mediante los PUT/DELETE existentes del nivel elegido; para keys
`org_only` el asignador DEBE ofrecer solo Organización; para keys `invariable`
la tarjeta DEBE mostrarse con candado y sin control (sección "Protecciones del
proveedor", D-36-09).
> **AC-036-003**: (E2E) asignar `allowed_tools` sin `web_search` a un usuario →
> PUT real del nivel user → la tarjeta del usuario refleja "Búsqueda en internet
> — Bloqueada" y `GET /api/policies/effective` confirma `web_search` ausente;
> `require_mfa` solo ofrece "Organización"; `approved_models_only` renderiza
> candado sin control y el PUT a cualquier nivel responde 422 (gate existente).

**REQ-036-004** (M) [D-36-03/04] (422 legible) — Cuando el control plane responde
422 (no-relajación o validación), la tarjeta DEBE mostrar una advertencia inline
legible que incluya el mensaje real del backend y una explicación en palabras del
principio de restrictividad ("un nivel inferior no puede relajar lo fijado por un
nivel superior"), conservando el valor anterior del control.
> **AC-036-004**: (E2E) intentar relajar a nivel usuario un valor fijado por la
> org → 422 real del control plane mostrado inline en la tarjeta con el mensaje
> del backend; el control vuelve al valor vigente; sin toast genérico.

**REQ-036-005** (M) [D-36-04/08] (vista por destinatario) — Cuando el admin entra
a un departamento o a un usuario desde el panel, el sistema DEBE mostrar sus
políticas como lista de frases legibles derivadas del valor efectivo computado
por el backend (p. ej. "Debe usar MFA ✓", "No puede buscar en internet ✓",
"Sus sesiones se guardan 15 días"), cada una con su control legible directo
(toggle/sub-toggles/select/…) y un badge de origen legible ("Heredada de la
organización" / "Establecida por el departamento" / "Establecida para este
usuario"); los multiselects DEBEN mostrarse como sub-toggles por ítem con estado
✓/✗ (D-36-08).
> **AC-036-005**: (E2E) usuario con `require_mfa=true` (org) y `allowed_tools`
> sin `web_search` (user) → la lista muestra "Must sign in with MFA — Inherited
> from organization" y "Web search — Blocked" con badge de origen user; togglear
> un ítem escribe por el PUT del nivel correcto y el badge se actualiza tras
> refetch real.

**REQ-036-006** (M) [transversal] (engine intacto) — La capa DEBE dejar intactos
el combinator deny-overrides, la validación de upsert (422), la no-relajación
(REQ-POL-007), el audit y el bundle JWS: cero mutaciones nuevas, cero cambios a
`combinator.py`/`policies.py`.
> **AC-036-006**: (review+CI) diff sin cambios en `combinator.py`/`policies.py`
> (salvo la carga del JSON de presentación, que es aditiva y read-only); suite
> governance completa verde; `GET /api/policies/effective` sin cambios de
> contrato.

**REQ-036-007** (M) [D-36-05/06/07] (robustez y bilingüe) — Cuando el catálogo
contenga una key sin `presentation`, la UI DEBE renderizarla con el control
genérico por `value_type` marcada "sin traducir" (sin romperse); los iconos
DEBEN ser lucide-react con fallback al icono de categoría; el contrato DEBE
estar completo en es+en.
> **AC-036-007**: (unit/test) con una key ficticia insertada en el catálogo de
> un tenant de test, matrix la sirve sin `presentation` y la UI (test de render)
> muestra el control genérico sin error; verificación de que los 47+9 iconos
> existen en lucide-react (script) o caen al fallback.

**REQ-036-008** (M) [spec 009] (estándar UI Pro) — El panel DEBE cumplir el
estándar UI Pro: design system existente (Card/Badge/Dialog/toasts/skeletons),
lucide-react, dark tokens, responsive, accesible (roles aria, labels, foco por
teclado), estados reales (loading/error/403/422).
> **AC-036-008**: (build+E2E) `npm run build` exit 0 y `npm run lint` limpios;
> navegación por teclado del panel funcional; estados loading/403 verificados.

**REQ-036-009** (M) [constitution I] (nada simulado) — Todo render del panel
DEBE provenir de respuestas reales de la API (`matrix`, `effective`, directorio
de usuarios/departamentos); prohibidos datos mock presentados como reales.
> **AC-036-009**: (review) grep sin literales de datos de policies en la página;
> evidencia E2E con login PKCE+TOTP real documentada en `docs/evidencia/policies-ux/`.

## 5. Amenazas y límites

- **Deriva del contrato**: mitigada por el test de cobertura 1:1 (AC-036-001) y
  por la copia única en plataforma (sha256 contra la spec).
- **Ítems dinámicos** (`item_source: connector_catalog|connections|models`): las
  etiquetas llegan de los endpoints ya existentes; si un slug no tiene etiqueta
  conocida se muestra tal cual (es un nombre, no un key técnico de policy).
- **Frases largas en es**: las plantillas están pensadas para tarjetas; si una
  frase excede el card, la UI trunca con tooltip (no acortar el contrato).
- El snapshot de efectiva sigue siendo de consulta; la verdad en el device es el
  bundle JWS (spec 019).

## 6. Trazabilidad

| Req | Padre | Componente |
|---|---|---|
| REQ-036-001 | REQ-033-001, REQ-029-001 | `platform/app/governance/` (carga del JSON + matrix) + test de cobertura |
| REQ-036-002 | REQ-033-003, spec 009 | `console/src/app/(console)/policies/` (panel legible) |
| REQ-036-003 | REQ-GOV-021…023, REQ-POL-004/007, `ORG_ONLY_KEYS` | ídem (asignador) |
| REQ-036-004 | REQ-POL-007 | ídem (manejo 422) |
| REQ-036-005 | REQ-033-002/004, D-33-02 | ídem (vista por destinatario) |
| REQ-036-006 | specs 005/029/019 | transversal (cero cambios de engine) |
| REQ-036-007 | D-36-05/06/07 | ídem + script de iconos |
| REQ-036-008 | spec 009 | ídem |
| REQ-036-009 | constitution I | ídem + docs de evidencia |

## 7. El contrato del catálogo legible

El archivo **`policy-catalog-readable.json`** (este directorio) es el entregable
de product-owner y la norma vinculante del catálogo: 47 keys × (nombre es/en,
descripción es/en, control legible con opciones en palabras, icono lucide-react,
categoría de 9, plantillas de frase). Validado el 2026-10-28 contra el estado
real (script de validación: cobertura 1:1 con `catalog.policy_keys` de la DB
viva, `org_only` == `ORG_ONLY_KEYS`, `invariable` == DB, enums ==
`STRING_RANKS`/`WEB_DOMAIN_MODES`/`MODE_RANKS`, campos completos por kind):
**47/47 OK**.

Ejemplos del mapeo (los tres de la visión de Miller):

| Frase de Miller | Key | Control legible |
|---|---|---|
| "Este usuario jamás podrá buscar en internet" | `allowed_tools` | sub-toggle "Búsqueda en internet" desmarcado |
| "Este usuario siempre debe usar MFA" | `require_mfa` | toggle "Exigir MFA a todos" (solo Organización) |
| "Este departamento solo puede usar los modelos aprobados X" | `allowed_models` | multiselect "Modelos de IA disponibles" → "Solo estos…" |

## 8. Convergencia

**Convergida el 2026-10-28** (ui, t_f0d2335a). Implementación == spec; los 9 AC
verificados con evidencia real en `docs/evidencia/policies-ux/evidencia-e2e.md`
(login PKCE+TOTP real, Chromium real, 16/16 checks DOM + 4/4 checks API +
suite backend verde):

| AC | Estado | Evidencia |
|---|---|---|
| AC-036-001 | ✅ | sha256 plataforma == contrato; test cobertura 1:1 47/47 (CI) |
| AC-036-002 | ✅ | panel por defecto, 9 categorías, 0 keys crudas, buscador es+en, conmutador solo org_admin (E2E Chromium) |
| AC-036-003 | ✅ | asignador Apply-to con PUT/DELETE existentes; web_search ausente en effective (source=user); require_mfa solo org (PUT user → 422); invariables con candado sin control (PUT → 422) |
| AC-036-004 | ✅ | 422 real inline (REQ-POL-007) + explicación en palabras; el control vuelve al valor vigente (input regresa a 5) |
| AC-036-005 | ✅ | frases desde el valor EFECTIVO del backend + badges de origen; sub-toggles ✓/✗ por ítem (D-36-08) |
| AC-036-006 | ✅ | diff vacío en combinator.py/policies.py; cero mutaciones nuevas; suite governance verde |
| AC-036-007 | ✅ | script de iconos 49/49 en lucide-react; test de tolerancia a key sin presentation (fallback "Untranslated") |
| AC-036-008 | ✅ | build exit 0; lint de policies limpio; Tab navegable; estados reales loading/error |
| AC-036-009 | ✅ | grep 0 frases hardcodeadas en la UI; evidencia E2E documentada |

Corrección incorporada durante el converge: los controles con borrador local
(number/domains/raw) ahora se remontan (`key={scopeKey#revision}`) al cambiar
de destinatario o al terminar una escritura — antes un 422 dejaba el borrador
stale en el input, violando "el control vuelve al valor vigente" (REQ-036-004).
