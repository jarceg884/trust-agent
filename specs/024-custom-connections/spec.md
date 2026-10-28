# Spec 024 — Conexiones personalizadas en el vault gobernadas por policy (mandato Miller)

> Estado: **specify** · Tarea kanban: t_b16825d0 · Fecha: 2026-10-26
> Idioma: español (docs) / inglés (código y UI).

## 1. Problema y mandato

Hoy el vault solo conoce credenciales de usuario vinculadas a un **catálogo fijo** de
conectores (`catalog.connector_catalog`: vmware/email/slack/teams/telegram/whatsapp) y la
policy las gobierna con `allowed_connectors` (slugs del catálogo) y
`vault_allowed_credential_types`. **No existe** el concepto de *conexión personalizada
definida por el tenant* (una base de datos, una API key genérica de un servicio interno,
etc.).

**Mandato de Miller**: en conexiones/vault hay que considerar conexiones PERSONALIZADAS
— bases de datos, API keys, etc. Esas conexiones se guardan en el vault y se ponen en la
policy: **el admin de la org decide qué conexiones agregar/permitir**.

## 2. Requisitos (EARS)

- **REQ-024-001** (CRUD): MIENTRAS el org_admin gestione conexiones, el SISTEMA DEBE
  permitir crear/actualizar/revocar conexiones personalizadas del tenant con: nombre
  único, `kind` (`database` | `api_key` | `custom`), metadata NO-secreta validada por
  kind, y un secreto cifrado con el envelope crypto existente (KEK/DEK AES-256-GCM).
- **REQ-024-002** (one-time): MIENTRAS se crea o actualiza el secreto de una conexión,
  el SISTEMA DEBE devolver el valor EXACTAMENTE una vez en esa respuesta; las lecturas
  (list/get) DEBEN devolver metadata únicamente — jamás secreto ni ciphertext.
- **REQ-024-003** (RBAC): MIENTRAS un rol sin permiso `connections.manage` (solo
  org_admin) intente crear/actualizar/revocar una conexión, el SISTEMA DEBE responder
  403 y auditar el intento según la matriz existente.
- **REQ-024-004** (audit): MIENTRAS una conexión se cree/actualice/revoque, el SISTEMA
  DEBE emitir `connection.created` / `connection.updated` / `connection.revoked` con
  payload SIN el secreto; el uso runtime emite `connection.used`.
- **REQ-024-005** (policy key): el SISTEMA DEBE ofrecer la clave tipada
  `allowed_connections` (list) en los 3 niveles org→depto→usuario con deny-overrides
  (intersección) y no-relajación entre niveles (maquinaria REQ-POL-006/007 sin cambios
  de semántica). Sus tokens son: `"*"` (todos los conectores del catálogo), slugs del
  catálogo, o el id (UUID) de una conexión personalizada ACTIVA del tenant. Un token
  desconocido/inexistente DEBE responder 422.
- **REQ-024-006** (default compatible): el default del proveedor de
  `allowed_connections` DEBE ser `["*"]`, de modo que los tenants existentes no cambian
  de comportamiento hasta que su admin fije la clave.
- **REQ-024-007** (uso de conexión personalizada): MIENTRAS un usuario use una conexión
  personalizada (inyección runtime del secreto, en memoria), el SISTEMA DEBE exigir que
  su id esté en la `allowed_connections` EFECTIVA (deny-overrides) → si no, 403
  `connector_not_allowed`; si está revocada, 410. La inyección es por-uso y sin estado
  persistente: un cambio de policy aplica en el uso SIGUIENTE (corte inmediato de
  facto, más fuerte que un sweep).
- **REQ-024-008** (catálogo + sweep): MIENTRAS `allowed_connections` cambie en
  cualquier nivel, el SISTEMA DEBE aplicar la regla combinada también a los conectores
  del CATÁLOGO (un slug es utilizable iff `slug ∈ allowed_connectors` Y (`"*" ∈
  allowed_connections` O `slug ∈ allowed_connections`)) y el sweep existente
  (`sweep_after_policy_change`) DEBE cortar en la misma transacción las instancias
  activas que la nueva policy efectiva ya no permite, con audit
  `connector.disconnected` (reason `policy_updated`) y notificación.
- **REQ-024-009** (aislamiento): la tabla de conexiones DEBE ser tenant-scoped con
  `tenant_id`, RLS ENABLE+FORCE con la política única `tenant_isolation` (fail-closed),
  índice tenant-first y verificación de aislamiento entre tenants.
- **REQ-024-010** (bundle): el governance bundle JWS DEBE llevar un claim ADITIVO
  `allowed_connections` con las conexiones efectivas del usuario (catálogo + las
  personalizadas permitidas, con id/kind/name/metadata NO-secreta), sin alterar los
  claims obligatorios §5.2 de la spec 019 — los bundles firmados y el verificador del
  plugin NO se rompen (schema versionado/extensible; coordinado con t_f99e12ab /
  t_c875626f).
- **REQ-024-011** (metadata por kind): el SISTEMA DEBE validar la metadata por kind:
  `database` → `{type, host, port, database, username}`; `api_key` → `{service, auth:
  header|bearer, header_name?}`; `custom` → jsonb libre. Toda clave de metadata que
  parezca un secreto (password/secret/token/api_key/credential) DEBE rechazarse con 422
  — el secreto vive cifrado en su columna.
- **REQ-024-012** (UI): la Admin Console DEBE ofrecer (a) CRUD de conexiones
  personalizadas en Credentials con secreto one-time, y (b) un selector de conexiones
  permitidas por nivel (catálogo + personalizadas) en Organization (org), Departments
  (depto) y Users (usuario).

## 3. Decisiones de diseño

- **D-24-01 — tabla propia `vault_connections`**: una conexión personalizada es un
  recurso de ORG (la define el admin), no una credencial de usuario. Reutiliza el
  envelope crypto y el patrón one-time del vault (spec 006) pero con visibilidad y
  ciclo propios. Columnas espejo de `vault_credentials` para el material cifrado.
- **D-24-02 — token de policy = id UUID**: el mandato pide ids de conexiones
  personalizadas; el UUID evita colisiones de nombre entre tenants y validaciones
  extra. El UI muestra nombre + kind y opera con el id.
- **D-24-03 — `"*"` como default**: sin wildcard, introducir la clave cortaría todos
  los conectores del catálogo de todos los tenants existentes. `"*"` = "todo el
  catálogo (sujeto a `allowed_connectors`)"; las conexiones personalizadas SIEMPRE
  requieren id explícito.
- **D-24-04 — permiso `connections.manage` solo a org_admin** (data-driven RBAC,
  mismo patrón que `roles.assign` de la spec 017): el admin de la org decide qué
  conexiones existen; dept_admins y users solo las consumen si la policy se los permite.
- **D-24-05 — sin instancias persistentes para conexiones personalizadas**: el secreto
  se inyecta por-uso (canal runtime del vault, REQ-VLT-003) y no queda sesión activa
  que cortar; la policy aplica en el uso siguiente y el bundle excluye la conexión en
  el sync siguiente. El sweep de REQ-024-008 cubre lo que SÍ tiene estado persistente:
  las instancias de conectores del catálogo.
- **D-24-06 — claim aditivo en el bundle**: `allowed_connections` se añade al payload
  del bundle como claim OPCIONAL (no está en REQUIRED_CLAIMS de spec 019 §5.2); el
  verificador del plugin basado en PyJWT ignora claims desconocidos → los bundles
  firmados con y sin el claim verifican igual (extensibilidad exigida por el
  entregable 4).
- **D-24-07 — validación de tokens en el upsert**: además del tipo list del catálogo,
  `allowed_connections` valida cada token contra `"*"` | catálogo | conexión activa
  del tenant (422 si no) — typo-protection coherente con REQ-POL-003 (catálogo tipado).

## 4. Criterios de aceptación

- **AC-24-001** (flujo completo, el de la tarjeta): custom creada por org_admin →
  secreto one-time → permitida en org (`allowed_connections` con su id) → depto la
  estrecha (la saca) → usuario del depto recibe 403 al usarla → usuario de otro depto
  la usa (200, inyección auditada `connection.used`) → removida de la policy org →
  uso 403 + sweep corta instancias de catálogo que la nueva policy efectiva ya no
  permite (audit `connector.disconnected`) → tipo de credencial no permitido 403/422,
  id no permitido 403, token desconocido en policy 422, no-org_admin CRUD 403.
- **AC-24-002** (one-time + metadata): GET de lista/detalle NO contiene secreto ni
  ciphertext; el secreto solo aparece en la respuesta de create/update.
- **AC-24-003** (audit): `connection.created/updated/revoked/used` presentes con
  payload sin secreto; cadena de audit íntegra (verify ok).
- **AC-24-004** (RLS): tenant B no ve las conexiones de tenant A (API y SQL); RLS
  ENABLE+FORCE + política `tenant_isolation` + índice tenant-first (suite db/tests).
- **AC-24-005** (bundle): el bundle JWS lleva `allowed_connections` con las
  personalizadas permitidas (id/kind/name/metadata) y el slugs del catálogo efectivo;
  un bundle firmado ANTES del cambio sigue verificando (claim aditivo).
- **AC-24-006** (UI real): CRUD desde la página Credentials y selector por nivel desde
  Organization/Departments/Users, con evidencia de navegador real (screenshots + OCR).

## 5. Fuera de alcance

- Drivers/conectividad real hacia las BDs externas (la plataforma entrega el secreto
  por inyección; el agente/consumer abre la conexión).
- Per-connection budgets o spend tracking (el presupuesto es del agente/usuario).
- Migración de `allowed_connectors` a `allowed_connections` (conviven: la segunda
  estrecha, nunca reemplaza — D-24-03).
