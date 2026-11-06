# Spec 037 — UX Shared Agents + Credentials: flujo lógico de primera intención y wizard de conexiones (repro Miller)

| Campo | Valor |
|---|---|
| **Estado** | Fase *implement* (SDD) — especificada e implementada en la misma tarea t_13b461a8 |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-28 |
| **Autor** | ui (tarea t_13b461a8) |
| **Padre** | specs/009-admin-console (REQ-AC-016/018), specs/020-default-agent-profiles, specs/024-custom-connections, specs/006-vault-connectors, specs/005-governance |
| **Idioma** | Español (docs) / inglés (UI copy y código) |

---

## 1. Problema (repro de Miller)

> "En share agents siento que no está lógico aún y poco user friendly. En
> credentials también muy poco user friendly."

Diagnóstico sobre las páginas vivas (pre-037):

- **/agents**: dos cards apiladas (perfiles + agentes) sin jerarquía; la policy
  efectiva vive escondida tras un botón "Effective policy"; adoptar es una fila
  de tabla, no una acción destacada; compartir exige **pegar el UUID del
  usuario**; revocar exige **pegar el grant ID** devuelto en un toast. Un
  usuario nuevo no sabe por dónde empezar.
- **/credentials**: tres cards con títulos técnicos ("Credential vault",
  "Application connectors", "Custom connections") sin separación conceptual
  catálogo-vs-propio; crear una conexión personalizada es un dialog monolítico
  de 8 campos; el estado de policy de cada conexión no es visible; labels con
  jerga ("Kind", "Scope", "metadata JSON").

El motor funciona (adopt → instancia del tenant; grants explícitos; secreto
one-time; policy deny-overrides). Esta spec cambia SOLO la presentación y añade
dos campos aditivos de solo-lectura al API.

## 2. Alcance

**Dentro de alcance**:

1. **Backend (aditivo, solo lectura)**: `allowed_for_caller: bool` en cada
   entrada de `GET /api/connectors` y de `GET /api/vault/connections`,
   computado con la policy EFECTIVA del caller (mismo engine deny-overrides
   que el runtime; cero mutaciones nuevas).
2. **Frontend /agents**: pestañas "Default profiles" / "My agents"; tarjetas de
   perfil con Adopt de un clic y policy efectiva visible INLINE; lista de
   agentes con dueño (nombre, no UUID), grants y estado; grants con selector
   de usuario real (`GET /api/users`) y revocación desde la lista de grants
   (`GET /api/agents/{id}/grants`) — cero pasteos de UUID.
3. **Frontend /credentials**: separación visual "Catalog connections" vs
   "My custom connections" (+ "My secrets" para el vault personal); wizard de
   3 pasos (tipo → datos → secreto one-time) para crear conexión; badge de
   policy por conexión ("Allowed by your organization" / "Blocked by policy");
   labels sin jerga.
4. **Evidencia E2E real** (browser + sesión PKCE+TOTP mintada): adoptar un
   perfil y crear una conexión con el wizard, sin errores, con screenshots.

**Fuera de alcance**:

- Cambios al combinator, gates de invoke, cifrado del vault, one-time delivery
  (specs 005/006/020/024 intactas).
- Nuevos endpoints de mutación (adoptar/grant/revoke/create usan los
  existentes).
- La página de policies (spec 036) y el desktop client.

## 3. Decisiones de diseño (vinculantes)

| ID | Decisión | Razón |
|----|----------|-------|
| **D-37-01** | **/agents se organiza en DOS pestañas con la default "Default profiles"** para un usuario sin agentes: (a) "Default profiles" — tarjetas del catálogo del producto con "Adopt" de un clic y la policy efectiva del caller visible inline (modelos aprobados + budget) SIN abrir dialog; (b) "My agents" — tabla con dueño por nombre, acceso, grants y estado. | Test del usuario nuevo: en 10 segundos ve tarjetas con un botón "Adopt" y una frase que le dice qué hacer. La pestaña inicial responde "¿por dónde empiezo?". |
| **D-37-02** | **Cero pasteos de UUID**: el dialog "Share" usa un selector de usuario real (`GET /api/users`, display_name + email, filtrable) y el dialog "Manage access" lista los grants reales (`GET /api/agents/{id}/grants`) con botón Revoke por fila. Ambos endpoints YA existen (spec 012 REQ-ORG-120). | El motor ya soporta la UX correcta; la página simplemente no los usaba. Nada simulado: nombres y grants vienen del API. |
| **D-37-03** | **El estado de policy por conexión lo computa el BACKEND** como campo aditivo `allowed_for_caller` (conector: `slug ∈ allowed_connectors` ∧ tokens `allowed_connections` lo cubren — misma regla `connection_tokens_allow` del runtime; conexión custom: `id ∈ allowed_connections`). La UI JAMÁS re-deriva deny-overrides. | Mismo argumento que D-36-04: la UI no re-implementa el combinator. El campo es de solo lectura y aditivo → cero riesgo de bypass. |
| **D-37-04** | **Wizard de 3 pasos para conexión personalizada**: (1) tipo con tarjetas opcionales grandes (Database / API key / Other), (2) datos del tipo elegido, (3) secreto con explicación one-time. Validación por paso, Back/Next, y el secreto se muestra UNA vez al final (OneTimeSecret existente). El dialog monolítico de 8 campos desaparece. | Paso a paso = sin vacilar; el secreto aislado en su paso refuerza el contrato one-time (constitution IV). |
| **D-37-05** | **/credentials separa visualmente dos mundos**: "Catalog connections" (grid de tarjetas: VMware, email… con Connect/Disconnect y badge de policy) y "My custom connections" (tabla + wizard + badge de policy), más "My secrets" (vault personal, labels llanos: "API keys and passwords for your own tools"). Labels sin jerga: "Type" (no "Kind"), "Connection details" (no "metadata JSON") salvo en "Other" donde el JSON libre es el contrato real (spec 024). | El repro de Miller es de comprensión: la separación catálogo-vs-propio es la primera pregunta que un admin se hace. |
| **D-37-06** | **Los conectores bloqueados por policy se muestran deshabilitados con hint** ("Ask an admin to allow this app"), no ocultos. | Ocultar sería opaco; el estado bloqueado es información de gobernanza visible (constitution V). |
| **D-37-07** | **El flujo Invoke mantiene su dialog con veredicto real** (200 autorizado / 403 model_not_approved / 403 budget_cutoff) pero con copy llano y el modelo preseleccionado del set aprobado. | El gate es el producto (spec 020 REQ-020-003); solo cambia la presentación. |

## 4. Requisitos (EARS)

Trazabilidad: serie **REQ-037**. Padre: specs 009/020/024.

### Shared Agents (/agents)

- **REQ-037-001** — La página /agents SHALL presentar dos pestañas
  ("Default profiles", "My agents") con contador; la pestaña activa por defecto
  SHALL ser "Default profiles" cuando el usuario no tiene agentes, para que un
  usuario nuevo vea primero las tarjetas adoptables.
- **REQ-037-002** — Cada tarjeta de perfil SHALL mostrar nombre, descripción,
  skills, modelos/conectores sugeridos, estado de adopción y un botón "Adopt"
  de un clic; tras adoptar, la tarjeta SHALL mostrar "Adopted" con enlace al
  agente en "My agents" (refetch de ambos lists).
- **REQ-037-003** — La pestaña "Default profiles" SHALL mostrar inline la
  policy efectiva del caller (modelos aprobados y estado de budget) sin
  requerir dialog, usando `effective_policy` de `GET /api/agent-profiles`.
- **REQ-037-004** — La tabla "My agents" SHALL mostrar dueño por nombre
  (resuelto vía `GET /api/users`; "You" para propios), acceso (Owner / Can
  invoke / Can view), número de grants activos (para agentes que el caller
  administra, vía `GET /api/agents/{id}/grants`) y estado.
- **REQ-037-005** — El dialog "Share agent" SHALL ofrecer un selector de
  usuario (display_name + email) filtrable alimentado por `GET /api/users` y
  permiso en lenguaje llano ("Can use it" / "Can view it"); NO SHALL exigir
  pegar un UUID.
- **REQ-037-006** — El dialog "Manage access" SHALL listar los grants activos
  (grantee por nombre, permiso, fecha) con botón "Revoke" por fila
  (`DELETE /api/agents/{id}/grants/{grant_id}`); NO SHALL exigir conocer el
  grant ID a priori.
- **REQ-037-007** — El dialog Invoke SHALL preseleccionar un modelo del set
  aprobado y SHALL mostrar el veredicto real del gate (200/403 con reason) en
  lenguaje llano.

### Credentials (/credentials)

- **REQ-037-008** — La página /credentials SHALL separar visualmente
  "Catalog connections" (grid de tarjetas del catálogo) de "My custom
  connections" (tabla de conexiones de la organización) y de "My secrets"
  (vault personal), en ese orden, con títulos y descripciones sin jerga.
- **REQ-037-009** — `GET /api/connectors` SHALL incluir `allowed_for_caller`
  (bool) por entrada, computado con la policy efectiva del caller (regla
  completa: `allowed_connectors` ∧ `allowed_connections` tokens).
- **REQ-037-010** — `GET /api/vault/connections` SHALL incluir
  `allowed_for_caller` (bool) por conexión (`id ∈ allowed_connections`).
- **REQ-037-011** — Cada conexión (catálogo y custom) SHALL mostrar un badge
  de policy: "Allowed by your organization" (verde) / "Blocked by policy"
  (rojo) derivado de `allowed_for_caller`; los conectores bloqueados SHALL
  mostrar Connect deshabilitado con hint "Ask an admin to allow this app".
- **REQ-037-012** — La creación de conexión personalizada SHALL ser un wizard
  de 3 pasos (tipo → datos → secreto) con indicador de progreso, validación
  por paso y entrega one-time del secreto al finalizar (OneTimeSecret).
- **REQ-037-013** — Los labels de la página NO SHALL usar jerga técnica
  ("Kind" → "Type", "Scope" → hint llano, "metadata" solo en "Other" con
  explicación); el copy SHALL explicar en una línea qué es cada sección.

### Evidencia

- **REQ-037-014** — Test de usabilidad E2E con browser real (sesión admin
  mintada PKCE+TOTP): flujo completo adoptar agente (REQ-037-002) y crear
  conexión con el wizard (REQ-037-012) SIN errores; evidencia en screenshots
  + OCR del texto visible.

## 5. Criterios de aceptación

| ID | Criterio | Verificación |
|----|----------|--------------|
| AC-037-1 | `npm run build` y `npm run lint` limpios en `platform/console` | Output real del comando |
| AC-037-2 | Tests de plataforma verdes, incl. los nuevos asserts de `allowed_for_caller` (connectors + connections) | `pytest tests/test_catalogs.py tests/test_custom_connections.py -q` |
| AC-037-3 | El stack vivo sirve los campos nuevos (`curl` con sesión real muestra `allowed_for_caller`) | Response real del API desplegado |
| AC-037-4 | E2E browser: adopt de perfil → tarjeta "Adopted" + agente en "My agents" con dueño "You"; wizard conexión 3 pasos → conexión visible con badge de policy; cero toasts de error | Screenshots + OCR |
| AC-037-5 | Un usuario nuevo entiende /agents en 10 s: la pestaña default muestra tarjetas con "Adopt" y la policy efectiva inline | Screenshot de la vista inicial |
