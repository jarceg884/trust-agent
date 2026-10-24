# Spec 006 — Vault de credenciales, conectores y mensajerías

| Campo | Valor |
|---|---|
| **Estado** | Fase *specify* → *plan* → *tasks* → *implement* → *converge* (SDD) |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-23 |
| **Autor** | dev (tarea t_4b540638) |
| **Padre** | specs/001-platform/spec.md §4.8/§4.9 (REQ-CON-001…006, REQ-VLT-001…007), docs/arquitectura.md §3.6/§5.5/§5.7/ADR-006/ADR-009 (contratos NORMATIVOS), specs/002-data-layer/spec.md (tablas `vault_credentials`, `connector_instances`, catálogos), specs/003-control-plane/spec.md (primitivas transversales) |
| **Idioma** | Español (docs) / inglés (código, UI copy) |

---

## 1. Alcance

Esta spec define la **API del vault de credenciales** y las **instancias de conectores** (incluidas mensajerías) sobre el núcleo de la spec 003 y el schema de la spec 002.

**Dentro de alcance**:

- **Vault** (arq. §5.5): registro de credenciales con envelope encryption (primitiva `app/vault/crypto.py` de spec 003), entrega del valor **one-time** (lección C1/C4, ADR-006), lectura de **solo metadatos**, rotación one-time, revocación con corte de conexiones, y **canal de inyección en runtime** para el agente (REQ-VLT-003).
- **Gobernanza del vault y conectores** (integración con el combinador deny-overrides de spec 003 / t07): `vault_allowed_credential_types` (REQ-VLT-004), `allowed_connectors` (REQ-CON-002), `messaging_permissions` por plataforma + modo (REQ-CON-004), scope mínimo del catálogo con `scoped_credentials` invariable (REQ-CON-006).
- **Instancias de conectores**: alta (con gobernanza), listado con visibilidad por rol, desconexión, reconexión, y la primitiva de corte `disconnect_connector_instances()` que la actualización de políticas (spec 005) invoca para REQ-CON-005.
- **Catálogo extensible** por datos (REQ-CON-001): `catalog.connector_catalog`; añadir un conector = INSERT, cero cambios de código.
- **Audit**: todo acceso a secretos del vault (registro, uso, rotación, revocación) y todo ciclo de vida de instancias (conexión, desconexión) queda en `audit_log` con hash chain (REQ-VLT-006).

**Fuera de alcance**:

- Endpoints de actualización de políticas y presupuestos, enforcement worker de cortes diferidos (→ spec 005-governance, t_3b4fb364). Esta spec entrega la **primitiva de desconexión** que ese plano invoca.
- Implementación física de los conectores (cliente IMAP, SDK de VMware, bots de mensajería): el control plane gobierna credenciales e instancias; el runtime del agente (desktop) consume el canal de inyección.
- Enrolamiento/aprobación de dispositivos (→ spec 007); keys BYOK consumidas por LiteLLM (→ spec 005, `REQ-MDL-007` usa este vault como almacén).
- Consulta/export del audit log (→ spec 008).

## 2. Decisiones de diseño (vinculantes)

| ID | Decisión | Fuente |
|---|---|---|
| D-VC-01 | **Entrega one-time estricta** (ADR-006): el valor en claro aparece EXCLUSIVAMENTE en la respuesta `201` de `POST /api/vault/credentials` y en la respuesta de `POST .../rotate`. `GET` (lista y detalle) devuelve solo metadatos; ningún log, audit payload ni export contiene el valor (grep de canario = 0, AC-VC-002). La rotación reemplaza el ciphertext (el valor anterior muere); no hay historial de valores. | REQ-VLT-002, C1/C4, arq. §5.5 |
| D-VC-02 | **Canal de inyección en runtime** (REQ-VLT-003): `POST /api/vault/credentials/{id}/use` es la vía por la que el runtime del conector (desktop/agente) recibe el secreto descifrado **en memoria**. Restricciones: propietario exclusivo (ni org_admin puede usar credenciales ajenas — la inyección es personal), credencial `active` (revocada → `410 gone`), instancia de conector `active` cuando la credencial tiene `connector_slug`, sesión con MFA. Marca `last_used_at` y emite `vault.credential_used` SIN el secreto en el payload. | REQ-VLT-003/005/006, arq. §3.6 |
| D-VC-03 | **Gobernanza deny-overrides en cada operación**: la política efectiva se calcula por request con `effective_policy_for_user` (spec 003, D-CP-15). Puertas: (a) tipo no ∈ `vault_allowed_credential_types` → `422 credential_type_not_allowed`; (b) conector no ∈ `allowed_connectors` → `403 connector_not_allowed` (aplica a registro de credencial con `connector_slug` Y a alta de instancia); (c) mensajería: plataforma no permitida por `messaging_permissions` → `403 connector_not_allowed`, modo superior al efectivo → `422 messaging_mode_not_allowed` (extensión documentada del contrato §5.2, misma forma que `permission_denied` en spec 003); (d) `scoped_credentials` es invariable `true` (piso del proveedor): toda credencial con conector exige `scope` no vacío y ⊆ `required_scopes` del catálogo → `422 validation_failed`. Mensajerías requieren AMBAS puertas (lista + map): deny-overrides, la más restrictiva gana. | REQ-VLT-004, REQ-CON-002/004/006, arq. §5.7 |
| D-VC-04 | **Visibilidad de metadatos por rol** (REQ-VLT-007): `user` ve sus propias credenciales/instancias; `dept_admin` las de los usuarios de sus departamentos (+ propias); `org_admin` todas las del tenant. Cross-tenant → 404 por RLS (D-CP-09). JAMÁS valores ni ciphertext en ninguna respuesta de lectura. | REQ-VLT-007, spec 001 §2.1 |
| D-VC-05 | **Rotación**: `POST .../rotate` con `{new_secret_value?}` opcional; si no viene, el servidor genera `secrets.token_urlsafe(32)` (operación de primera clase, arq. ADR-006 "copiar ahora o rotar después"). Solo el propietario rota (la respuesta entrega el nuevo valor). Re-cifra con DEK fresca + nonce fresco; `last_rotated_at = now()`; audit `vault.credential_rotated`. | REQ-VLT-002, ADR-006 |
| D-VC-06 | **Revocación con corte inmediato** (≤ 60 s, REQ-VLT-005): `POST .../revoke` marca la credencial `revoked` y EN LA MISMA TRANSACCIÓN desconecta (`status='disconnected'`) toda instancia activa que la use, con un evento `connector.disconnected` por instancia + `vault.credential_revoked`. Puede revocar: propietario, dept_admin del departamento del propietario, org_admin. Uso posterior → `410 gone`. | REQ-VLT-005, arq. §3.9 |
| D-VC-07 | **Instancias de conector** (`connector_instances`, UNIQUE `(tenant,user,slug)`): alta `POST /api/connectors/instances` con gobernanza D-VC-03; si existe fila `disconnected` se **reactiva** (200) con la nueva config; si existe fila `active` → `409 conflict`; `revoked` es terminal → `409`. La credencial vinculada debe ser del propietario, `active`, y coincidir en `connector_slug` (422). `mode` aplica a mensajerías (gobernado por el map); para el resto de categorías default `read_only`. `DELETE /api/connectors/instances/{id}` desconecta (idempotente) + audit `connector.disconnected`. La `config` de la instancia es NO-secreta por schema (002 §5.11): el API rechaza (422) claves de config de aspecto secreto (`password`, `secret`, `token`, `api_key`, `credential`) — los secretos viven en el vault (C4, REQ-SEC-005). | REQ-CON-002/004, schema 002 §5.11 |
| D-VC-08 | **Catálogo extensible por datos** (I10): ni slugs ni categorías ni scopes viven en código; todo sale de `catalog.connector_catalog`. Test de extensibilidad: INSERT de un conector 7.º lo lista sin cambio de código (y se limpia al final para no romper AC-CP-054). | REQ-CON-001, I10 |
| D-VC-09 | **Audit de todo acceso a secretos** (REQ-VLT-006): eventos `vault.credential_created` / `vault.credential_used` / `vault.credential_rotated` / `vault.credential_revoked` / `connector.connected` / `connector.disconnected` (todos existen en `catalog.audit_actions`, schema 002 §3.5) con `actor_id`, `target_type`, `target_id` y payload SIN secreto. La enumeración de REQ-VLT-006 (registro, uso, rotación, revocación) es la norma: el listado de metadatos no audita (no es acceso a secreto). La cadena sigue verificando tras los eventos. | REQ-VLT-006, schema 002 §5.12 |
| D-VC-10 | **Cifrado**: reutiliza la primitiva `app/vault/crypto.py` (spec 003, D-CP-14) sin modificación: DEK aleatoria por credencial, KEK de `TRUST_VAULT_KEK_B64` (nunca en DB), `key_id` versionado de `TRUST_VAULT_KEY_ID`. Dump de `vault_credentials` sin la KEK es criptográficamente inútil. | REQ-VLT-001, ADR-009 |

## 3. Requisitos (formato EARS)

Convenciones: plantilla EARS **Cuando** `<trigger>` **el sistema** `<respuesta>`; prioridad MoSCoW; cada REQ con AC verificable con evidencia real. Trazabilidad: `[001-REQ-…]` = spec maestra, `[ARQ-…]` = arquitectura, `[003-D-CP-…]` = spec control plane.

### 3.1 Vault de credenciales

**REQ-VC-001** (M) [001-REQ-VLT-001, ARQ-ADR-009] — Cuando el sistema almacena una credencial, la cifra en reposo con envelope encryption (DEK por credencial, KEK fuera de la DB); en la DB solo hay `ciphertext_secret`, `ciphertext_dek`, `nonce` y `key_id`.
> **AC-VC-001**: tras un registro real, la fila en DB no contiene el valor en claro (grep del canario en la fila = 0); `decrypt_secret` con la KEK recupera el valor exacto; con una KEK distinta el descifrado falla (InvalidTag).

**REQ-VC-002** (M) [001-REQ-VLT-002, C1/C4, ARQ-ADR-006] — Cuando un usuario registra o rota un secreto, el sistema devuelve el valor **una única vez** en esa respuesta; ninguna respuesta posterior, log ni audit payload lo contiene jamás.
> **AC-VC-002**: `201` de registro y `200` de rotación incluyen `secret_value`; `GET` lista y detalle no contienen el canario (assert sobre el texto completo de la respuesta); el payload de TODOS los eventos de audit del flujo no contiene el canario; los logs capturados del test (caplog) no contienen el canario.

**REQ-VC-003** (M) [001-REQ-VLT-003, ARQ-§3.6] — Cuando el runtime del agente necesita una credencial, el sistema la entrega descifrada SOLO por el canal de inyección `POST /api/vault/credentials/{id}/use`, restringido al propietario con credencial activa (y instancia activa si la credencial es de un conector), actualiza `last_used_at` y audita `vault.credential_used` sin el secreto.
> **AC-VC-003**: uso por el propietario → 200 con el valor exacto + `last_used_at` fijado + evento de audit; uso por otro usuario del tenant → 404; uso cross-tenant → 404; uso con credencial revocada → 410; el payload del evento no contiene el canario.

**REQ-VC-004** (M) [001-REQ-VLT-004] — Cuando un usuario registra una credencial de un tipo que su política efectiva `vault_allowed_credential_types` no permite, el sistema la rechaza con `422 credential_type_not_allowed`.
> **AC-VC-004**: usuario con tipo restringido a nivel user (deny-overrides sobre org permisiva) registra tipo no permitido → 422 con ese reason; tipo permitido → 201.

**REQ-VC-005** (M) [001-REQ-VLT-005] — Cuando se revoca una credencial, el sistema la marca `revoked`, desconecta en la misma transacción toda instancia activa que la use (corte ≤ 60 s — inmediato), y audita `vault.credential_revoked` + un `connector.disconnected` por instancia.
> **AC-VC-005**: tras revoke, la credencial queda `revoked`, la instancia `disconnected`, existen ambos eventos de audit, y el uso posterior responde 410.

**REQ-VC-006** (M) [001-REQ-VLT-006] — Cuando se accede a un secreto (registro, uso, rotación, revocación), el sistema emite el evento de audit correspondiente con actor, target y timestamp; la cadena hash del tenant sigue verificando.
> **AC-VC-006**: los 4 tipos de evento existen en `audit_log` tras el flujo completo con `actor_id` correcto; `audit_verify_chain` del tenant → `{"status": "ok"}`.

**REQ-VC-007** (M) [001-REQ-VLT-007] — Cuando alguien consulta el vault, el sistema devuelve solo metadatos (`id`, `name`, `credential_type`, `connector_slug`, `scope`, `status`, `last_rotated_at`, `last_used_at`, `created_at`, `user_id`), nunca valores ni ciphertext, con visibilidad por rol (D-VC-04).
> **AC-VC-007**: el detalle contiene exactamente las claves de metadatos (ni `secret*` ni `ciphertext*` ni `nonce`); `user` no ve credenciales ajenas; `org_admin` ve las del tenant; cross-tenant → 404.

**REQ-VC-008** (M) [001-REQ-VLT-002] — Cuando el propietario rota una credencial, el sistema genera o recibe el nuevo valor, lo devuelve una única vez, destruye el valor anterior (re-cifrado con DEK/nonce frescos) y audita `vault.credential_rotated`.
> **AC-VC-008**: rotación sin cuerpo → 200 con `secret_value` aleatorio ≠ anterior; el descifrado posterior (vía use) devuelve el NUEVO valor; `last_rotated_at` fijado; evento de audit presente; GET no expone el nuevo valor.

### 3.2 Conectores y mensajerías

**REQ-VC-010** (M) [001-REQ-CON-001, I10] — Cuando se consulta el catálogo global, el sistema lista los conectores desde `catalog.connector_catalog` (set inicial de 6: vmware, email, whatsapp, telegram, slack, teams) con nombre, categoría, capacidades, scopes requeridos y estado; la estructura es extensible por datos.
> **AC-VC-010**: `GET /api/connectors` lista los 6 (AC-CP-054 ya lo cubre); test de extensibilidad: INSERT de un conector 7.º (cleanup al final) aparece en el listado sin cambio de código.

**REQ-VC-011** (M) [001-REQ-CON-002/003] — Cuando un usuario conecta una instancia de un conector, el sistema valida su política efectiva `allowed_connectors` (403 `connector_not_allowed` si no), valida la gobernanza de mensajería si aplica (D-VC-03), vincula la credencial del propietario y crea/reactiva la instancia; el desktop puede listar solo los conectores permitidos vía `GET /api/connectors/available`.
> **AC-VC-011**: conector permitido → 201; no permitido → 403 `connector_not_allowed`; duplicado activo → 409; `available` devuelve exactamente los slugs de la política efectiva del principal.

**REQ-VC-012** (M) [001-REQ-CON-004] — Cuando un usuario conecta una mensajería, el sistema aplica `messaging_permissions` efectiva: plataforma prohibida (o ausente) → 403; modo solicitado superior al efectivo → 422 `messaging_mode_not_allowed`; el modo queda fijado en la instancia.
> **AC-VC-012**: plataforma denegada a nivel user sobre org permisiva → 403 (deny-overrides); modo `full` con política `read_only` → 422; modo `read_only` → 201 con `mode` persistido.

**REQ-VC-013** (M) [001-REQ-CON-005] — Cuando una vía de gobernanza debe cortar conectores (revocación de credencial, deshabilitación por política desde spec 005), el sistema desconecta las instancias activas afectadas en la misma transacción (≤ 60 s) mediante la primitiva `disconnect_connector_instances()` y audita cada corte.
> **AC-VC-013**: la revocación de una credencial usada por una instancia activa la desconecta en la misma transacción con evento de audit (cubierto por AC-VC-005); la primitiva opera dentro del scope de tenant de la transacción (D-CP-02) con filtros `(connector_slug, credential_id?, user_id?)` y solo toca instancias `active`.

**REQ-VC-014** (M) [001-REQ-CON-006] — Cuando un usuario registra una credencial de un conector, el sistema exige scope no vacío y subconjunto de los `required_scopes` del catálogo (`scoped_credentials` es invariable: piso del proveedor).
> **AC-VC-014**: scope `"admin"` para vmware (`required_scopes=["read_only"]`) → 422; scope `"read_only"` → 201; scope ausente con conector → 422; credencial sin conector (p. ej. BYOK) no exige scope.

**REQ-VC-015** (M) [001-REQ-CON-002, 003-D-CP-09] — Cuando un usuario accede a instancias o credenciales de otro tenant por ID forjado, el sistema responde 404 sin revelar existencia (RLS + traducción a not_found).
> **AC-VC-015**: usuario del tenant B usa un `credential_id`/`instance_id` del tenant A → 404; el listado del tenant B no contiene filas del A.

## 4. Contrato de API (resumen normativo)

| Método y ruta | Permiso | Efecto |
|---|---|---|
| `GET /api/connectors` | sesión MFA | Catálogo global (spec 003, sin cambios) |
| `GET /api/connectors/available` | sesión MFA | Catálogo filtrado por `allowed_connectors` efectiva (+ modo de mensajería) |
| `POST /api/connectors/instances` | sesión MFA | Alta/reactivación con gobernanza; audit `connector.connected` |
| `GET /api/connectors/instances[/{id}]` | sesión MFA | Metadatos con visibilidad por rol |
| `DELETE /api/connectors/instances/{id}` | sesión MFA (propietario/dept_admin/org_admin) | Desconexión; audit `connector.disconnected` |
| `POST /api/vault/credentials` | sesión MFA | Registro cifrado; `201 {credential_id, secret_value, metadata}` one-time |
| `GET /api/vault/credentials[/{id}]` | sesión MFA | SOLO metadatos, visibilidad por rol |
| `POST /api/vault/credentials/{id}/rotate` | propietario | Nuevo valor one-time; audit `vault.credential_rotated` |
| `POST /api/vault/credentials/{id}/revoke` | propietario/dept_admin/org_admin | Revocación + corte de instancias; audit |
| `POST /api/vault/credentials/{id}/use` | propietario | Canal de inyección runtime; audit `vault.credential_used` |

Envelope de errores §5.2 en TODO el API; reasons nuevos: `connector_not_allowed` (403), `credential_type_not_allowed` (422), `messaging_mode_not_allowed` (422, extensión documentada); revocada en uso → `gone` (410).

## 5. Trazabilidad con lecciones de la auditoría AIDLC

| Lección | Cómo la cierra esta spec |
|---|---|
| C1 (password en respuesta persistente) | D-VC-01: entrega one-time solo en registro/rotación; GET = metadatos; AC-VC-002 con grep de canario |
| C4 (secretos en claro en archivos/DB) | D-VC-10: envelope encryption, KEK fuera de DB; AC-VC-001 |
| I6 (scoped credentials) | D-VC-03(d): scope mínimo del catálogo, invariable |
| I10 (catálogos = datos) | D-VC-08: catálogo de conectores por datos, extensible sin código |
