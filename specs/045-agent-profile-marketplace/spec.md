# Spec 045 — Marketplace de perfiles de agente (Admin Console → Agentes)

| Campo | Valor |
|---|---|
| **Padre** | specs/020-default-agent-profiles · specs/037-shared-agents-credentials-ux · specs/041-named-policies · spec 044 (perfiles `{name, model}` en la política, implementada en `app/policy_bundles/router.py`) · specs/019 (governance bundle firmado) · specs/040-platform-installers |
| **Alcance** | (1) La sección **Agentes** del Admin Console pasa a ser un **marketplace de perfiles de Hermes**: el admin importa perfiles exportados con `hermes profile export` y la plataforma los sanea, versiona y cataloga. (2) La **política** asigna perfiles del marketplace (cada uno con su modelo) y marca **un perfil por defecto**. (3) El **desktop gobernado** materializa esos perfiles: el agente del empleado ES ese perfil de Hermes. |
| **Fuente** | Mandato Miller (12-nov-2026): *"en el apartado agentes … un market place de perfiles … uno en esa sección pueda importar perfiles de hermes, y cuando asigno la política el agente sea ese perfil de hermes, incluso que dentro de la política se pueda asignar un perfil default"*. Decisiones cerradas en la sesión de clarificación (§0). |
| **Reglas** | Nada simulado: aceptación sobre el MSIX instalado y con exports reales de Hermes. *Desktop = Hermes configured, never a patched fork*: el equipo usa la mecánica de perfiles/distributions de Hermes. Credenciales jamás en paquetes, respuestas, logs ni auditoría. Zero data loss: memorias y sesiones del empleado nunca se borran por una acción de política. Español en la UI. |

## 0. Decisiones de producto (clarificación 12-nov-2026)

| ID | Pregunta | Decisión |
|---|---|---|
| D-045-A | Agentes compartidos (grants) y plantillas Analyst/Coder/Ops | **El marketplace los reemplaza.** Analyst/Coder/Ops pasan a ser perfiles **de fábrica** del marketplace. |
| D-045-B | Varios perfiles en una política | El empleado ve **todos los perfiles asignados** en el selector y la app **arranca en el default**. Crear perfiles propios sigue dependiendo del switch **Perfiles**. |
| D-045-C | MCP servers y cron dentro del perfil exportado | **Se conservan tal cual** en el paquete. En el equipo la política sigue decidiendo (P-6/P-9, §6.4). |
| D-045-D | La política deja de incluir un perfil | **Se archiva** en el equipo: desaparece de la app y conserva memorias y sesiones por si vuelve. |
| D-045-E | Reimportar un perfil (nueva versión) | Las políticas usan **siempre la última versión**; los equipos se actualizan solos en el siguiente sync **conservando memorias y sesiones**. |

## 1. Catálogo del marketplace (D-045-01)

Un perfil del marketplace es un **paquete saneado de Hermes** más sus metadatos.

- `agent_profiles` (RLS por `tenant_id`): `id`, `tenant_id`, `slug` (id canónico de Hermes, `^[a-z0-9][a-z0-9_-]{0,63}$`, único por tenant), `name`, `description`, `source` (`imported` | `factory`), `factory_key` (solo fábrica), `status` (`active` | `archived`), `created_by`, `created_at`, `updated_at`.
- `agent_profile_versions`: `id`, `profile_id`, `version` (entero creciente), `sha256`, `size`, `manifest` (jsonb: archivos, skills, extracto de `SOUL.md`, MCP servers, cron jobs, secretos requeridos por nombre, qué se eliminó, redacciones), `imported_by`, `imported_at`. **Vigente = la versión más alta** (D-045-E).
- Paquetes en disco **direccionados por contenido**: `<agent-profiles-dir>/<sha256>.tar.gz`, de solo lectura una vez escritos (mismo patrón que `installer-artifacts`). Un mismo contenido no se duplica entre tenants ni versiones.
- **Perfiles de fábrica** (D-045-A): Analyst, Coder y Ops se construyen como **perfiles reales de Hermes** con la API de perfiles de Hermes, la misma que ejecutan `hermes profile create` y `hermes profile export`. El script es `platform/scripts/build_factory_profiles.py` y corre en un HOME aislado. Cada perfil lleva el `SOUL.md` de su rol y cuatro skills incluidas en Hermes. Los exports viven en `app/profile_marketplace/factory/` y entran por el mismo pipeline de importación (§2). Se siembran de forma idempotente en el marketplace de cada tenant (`source='factory'`, keyed por `factory_key`) al primer acceso, y antes de cualquier importación. Un paquete de fábrica nuevo, en un release del producto, se convierte en versión nueva; un perfil de fábrica archivado sigue archivado.
- **API**: `/api/marketplace/profiles`. `/api/agent-profiles` sigue siendo de las plantillas de la spec 020 mientras la consola migra (D-045-08). El cuerpo de error lleva `rule` en el nivel superior del envelope (contrato de la spec 041).

> **AC-045-01**: un tenant nuevo ve Analyst, Coder y Ops en su marketplace sin hacer nada. Los tres provienen de exports reales de Hermes y se pueden asignar en una política.

## 2. Importación y saneamiento (D-045-02)

`POST /api/marketplace/profiles/import` (cuerpo = el `.tar.gz`, `?slug=` opcional, **org_admin**). Acepta el `.tar.gz` de `hermes profile export` (un único directorio raíz = nombre del perfil), incluidos los que traen `distribution.yaml`. El archivo es **contenido no confiable**.

**Rechazo** (error claro, nada se guarda): más de un directorio raíz; rutas absolutas o con `..`; symlinks, hardlinks, dispositivos o FIFOs; nombre raíz fuera de `PROFILE_ID_RE`; límites superados (comprimido 50 MB, descomprimido 200 MB, 5.000 archivos, 20 MB por archivo); gzip/tar corrupto.

**Se conserva** (lo que define al agente, semántica *distribution-owned* de Hermes): `SOUL.md`, `config.yaml` (saneado), `skills/**`, `mcp.json`, `cron/jobs.json` (D-045-C), `distribution.yaml`, `.env.template`/`.env.EXAMPLE` (solo nombres de secretos requeridos, nunca valores).

**Se elimina siempre**:
- credenciales en cualquier profundidad: `.env`, `auth.json`, `auth/`, `.op.env`, `*oauth*.json`, almacenes del SO (`.ssh`, `.gnupg`, `.aws`, `.config/gh`…);
- datos del usuario: `memories/`, `sessions/`, `state.db*`, `logs/`, `plans/`, `workspace/`, `home/`, `backups/`, `cache/`, `local/`, `checkpoints/`, `state-snapshots/`;
- código y marca: `plugins/` (el equipo instala su propio plugin de gobernanza), `skins/` (la marca es del tenant), `bin/`, bookkeeping de skills (`skills/.hub`, `.usage.json`, locks).

**`config.yaml`**: se eliminan las claves que la gobernanza fija (`model`, `providers`, `fallback_providers`, `provider_routing`, `auxiliary`, `local_runtime`, `moa`, `gateway`, `plugins`). El resto se conserva, incluido `mcp_servers` (D-045-C).

**Secretos**: los archivos de texto se re-escanean con los patrones de redacción de Hermes. Un secreto se redacta y se cuenta, y el conteo se muestra en la ficha.

**Salida**: tar.gz **canónico y determinista** (entradas ordenadas, mtime fijo, uid/gid 0) → `sha256` → almacenamiento. Si el slug ya existe en el tenant, se crea una **versión nueva**; si no, un perfil nuevo.

> **AC-045-02**: importar un export real de Hermes que contiene `.env`, `auth.json`, `memories/` y `sessions/` produce un paquete sin ninguno de ellos y sin secretos (verificado listando el paquete almacenado). La ficha muestra qué se eliminó.
>
> **AC-045-03**: archivos maliciosos (path traversal, symlink, dos raíces, bomba de tamaño) se rechazan con un mensaje claro y sin escribir nada.

## 3. Ficha, versiones y ciclo de vida (D-045-03)

- **Ficha**: nombre, descripción, versión vigente e historial, importado por/cuándo, skills (nombres), extracto de `SOUL.md`, **MCP servers y cron jobs incluidos** (visibles: el admin sabe qué ejecutará el perfil), secretos requeridos (nombres), qué se eliminó, en qué políticas se usa.
- **Acciones** (org_admin): importar, importar nueva versión, editar nombre/descripción, descargar el paquete saneado y archivar. Si alguna política usa el perfil, se muestra la lista y se pide confirmar; al archivar, los equipos lo archivan en el siguiente sync. dept_admin ve el marketplace y asigna perfiles a las políticas de su departamento.
- **Auditoría**: `agent_profile.imported`, `.version_added`, `.updated`, `.archived`, sin contenido de archivos.

## 4. Asignación en la política (D-045-04)

- `profiles: [{profile_id, model}]` y `default_profile_id`. Validación en el servidor (el bundle viaja firmado): perfil existente y activo del mismo tenant, sin duplicados, `model ∈ models` de la política (regla de spec 044), `default_profile_id ∈ profiles` y obligatorio si hay perfiles.
- La regla de spec 044 v2 no cambia: una política pertenece a **un departamento o una persona**, y sus empleados reciben sus perfiles.
- La forma `{name, model}` de spec 044 se reemplaza. Hoy ninguna política la usa (verificado en la DB 12-nov), así que no hay migración de datos.

> **AC-045-04**: el admin crea una política para un departamento con dos perfiles del marketplace y marca uno como default. Guardar con un modelo fuera de la política o sin default falla con un mensaje claro.

## 5. Bundle y descarga autenticada (D-045-05)

- El bundle firmado lleva `policy_profiles: [{profile_id, slug, name, version, sha256, size, model, default}]`.
- `GET /api/me/agent-profiles/{profile_id}/package`, con la misma auth que el governance bundle (device token). Solo sirve perfiles presentes en el bundle vigente del dispositivo; cualquier otro responde 404. Devuelve el paquete saneado con `ETag = sha256`.
- **Edge público :3100**: la ruta entra en la allowlist (`console/src/lib/edge-routes.ts`) con su test. Sin eso, un equipo externo recibe 404.

> **AC-045-05**: un dispositivo de otro tenant, o uno cuya política no incluye el perfil, recibe 404. Desde fuera de la LAN (edge) la descarga funciona y el sha256 coincide con el del bundle firmado.

## 6. Materialización en el equipo (D-045-06)

En cada sync, el plugin de gobernanza recorre `policy_profiles`.

1. **Instalar o actualizar**: si falta el perfil o cambió su `sha256`, descarga el paquete, verifica el sha256 contra el bundle **firmado** (un paquete alterado en tránsito se rechaza), extrae con las mismas reglas de seguridad de §2 a un staging y sustituye **solo** las rutas del paquete (staging + rename, nunca a medias). Memorias, sesiones, `state.db` y workspace del empleado se conservan (semántica `update_distribution` de Hermes; `config.yaml` es de la organización). Luego copia el plugin de gobernanza, fija `plugins.enabled` y aplica P-26 (`_normalize_profile_configs`: proveedor, endpoint y clave de la organización; modelo de la política).
2. **Marcador**: `<perfil>/.trust-agent-managed.json` con `{profile_id, version, sha256}`. Solo los perfiles con marcador son de la organización. **Un perfil creado por el empleado nunca se toca.** Si su nombre coincide con el slug, el perfil de la organización se instala como `<slug>-org`.
3. **Default** (D-045-B): la app arranca en el perfil default. Se aplica al instalarlo y cada vez que la política cambia el default, no en cada sync, para no pelear con un cambio manual del empleado durante la sesión.
4. **MCP y cron** (D-045-C): se conservan tal cual y la política decide en runtime, como hoy. P-6 veta las tools `mcp__<server>__*` fuera de `allowed_mcp_servers`; P-9 veta `cronjob_manage` y corrige `cron.allow_agent_scheduling`. **Además**, el plugin desactiva en la config del perfil los `mcp_servers` que la política no permite, para que **el proceso** del server no arranque (hoy solo se veta la tool). También verifica que el scheduler no ejecute jobs importados con `allow_scheduled_tasks=false` y, si lo hace, extiende P-9.
5. **Archivar** (D-045-D): si un perfil gestionado sale de la política, o se archiva en el marketplace, se mueve con sus memorias y sesiones a `trust-agent/archived-profiles/<slug>@<timestamp>` y la app deja de listarlo. Si vuelve a asignarse, se restaura el archivo más reciente del mismo `profile_id` y se actualiza a la versión vigente.
6. **Sin red**: se conserva la versión instalada, se reintenta en el siguiente sync y se registra el evento. Nunca queda un perfil a medio escribir.

> **AC-045-06** (app instalada): un empleado del departamento abre la app y ve los dos perfiles en el selector. Arranca en el default, y un chat real en ese perfil responde con su persona (`SOUL.md`) y usa una de sus skills, con el modelo que fija la política.
>
> **AC-045-07**: reimportar el perfil como v2 → en el siguiente sync el equipo tiene la v2 y conserva las sesiones y memorias creadas con la v1.
>
> **AC-045-08**: quitar un perfil de la política → desaparece de la app y sus datos quedan archivados. Volver a asignarlo → reaparece con su historial.

## 7. Desktop (D-045-07)

- El selector de perfiles (riel/dropdown) se muestra cuando el bundle asigna perfiles, aunque el switch **Perfiles** esté OFF. Lista los perfiles asignados, más los propios del empleado solo si el switch está ON.
- Crear perfiles depende del switch **Perfiles** (sin cambios). Importar perfiles sigue denegado en el equipo (`profile_import`): la vía de entrada es el marketplace.
- El perfil raíz (host técnico del gateway) no aparece en el selector cuando la política asigna perfiles. **Validado (12-nov):** es viable como filtro de PRESENTACIÓN; el raíz no se puede quitar de `$profiles` ni de Hermes porque hospeda el gateway multiplexado y es la fuente de clonado del diálogo gobernado.
- Requiere nueva versión del plugin y del MSIX (regla: no subir `plugin.yaml` sin MSIX).

### 7.1 Implementación (plugin 0.7.0 + MSIX 0.7.0)

**Plugin** — `trust-agent-governance/profile_delivery.py`, llamado por el `BundleSyncer` con cada bundle verificado (nunca lanza):
- Descarga `GET /api/me/agent-profiles/{id}/package` con el token del dispositivo, sha256 y tamaño contra el bundle firmado, extracción estricta a `trust-agent/profile-staging` (la forma canónica exacta: `KEEP_ROOT_FILES`, `skills/**`, `cron/jobs.json`; sin links, traversal, nombres reservados de Windows ni colisiones de mayúsculas). Una instalación nueva se arma entera en staging —config gobernado (P-26, modelo de la política, `plugins.enabled`), plugin copiado, MCP/cron ya aplicados, marcador— y aparece con UN rename; Hermes la sirve por el rescan del multiplexor (`notify_multiplexer_profiles_changed`).
- Actualizar: cada archivo del paquete se sustituye de forma atómica; lo que la versión anterior traía y esta no se borra (lista `files` del marcador); memorias, sesiones, `state.db`, workspace y skills propias del empleado no se tocan. El marcador se escribe al final: una actualización interrumpida se repite entera.
- Marcador `.trust-agent-managed.json`: `{profile_id, slug, dir, version, sha256, model, name, description, files, cron_jobs, cron_paused, mcp_disabled, installed_at, updated_at}`. `profile.yaml` lleva nombre y descripción del marketplace (el admin los edita sin versión nueva).
- MCP: `mcp_servers.<server>.enabled: false` (la clave que leen todos los lectores de Hermes) para los no permitidos; se reactiva solo lo que apagó el plugin.
- Cron: con la API de Hermes (`cron.jobs` con `use_cron_store`: lock del store y `next_run_at` correcto). Los jobs nuevos llegan pausados (`import_job_definitions`); con `allow_scheduled_tasks=false` quedan pausados con el motivo de la organización y con `true` se reanudan solo los que pausó la organización (una pausa del empleado se respeta).
- Archivar = el mecanismo de `hermes profile delete`: tombstone (`profiles/.deleted/<name>`: ni servido ni listado), rescan, `wait_profile_unowned`, rename a `trust-agent/archived-profiles/<slug>@<ts>` y fuera el tombstone. En Windows, si el gateway aún tiene abierta la base, el perfil queda oculto y el archivo se reintenta en el siguiente sync.
- Publica `trust-agent/managed-profiles.json`: `{profiles: [{profile_id, slug, name, dir, version, sha256, model, default, status}], default: {profile_id, dir}, default_key, default_seq, archive_pending}`; `default_seq` sube solo cuando cambia el default instalado.

**Desktop** — `electron/governed-profiles.ts` lee ese archivo (solo perfiles vivos: marcador presente y sin tombstone); IPC `hermes:governance:managed-profiles` (async), `-sync` (primer pintado) y el aviso `-changed` (polling de `fs.watchFile`, el plugin reemplaza el archivo con rename). El perfil de arranque se aplica una vez por `default_seq` con la preferencia de Electron (`desktopProfilePreferences.setDefault`) antes de leer la ruta de inicio; en caliente solo se cambia una ventana que sigue en el raíz oculto (primera instalación): un cambio de default posterior aplica al siguiente arranque y nunca re-ubica una conversación abierta. Renderer: `src/store/governed-profiles.ts` (filtro puro, probado) en el riel, el dropdown, el sidebar y la barra de estado; crear y Manage siguen gateados por el switch Perfiles; en los perfiles de la organización se ocultan renombrar, SOUL, exportar, borrar y remoto, y `hermes:api` responde 403 `profile_managed_by_organization` a renombrar/borrar uno.

## 8. Reemplazo de Agentes compartidos (D-045-08)

- La página **Agentes** pasa a ser el marketplace. La UI de agentes compartidos y grants se retira.
- **Sin migraciones destructivas**: las tablas `agents`/`agent_grants` y su API se conservan (solo lectura, en desuso). Hoy hay 1 agente activo en e2e-lab y 1 adoptado de plantilla en demo-miller; se reportan, no se borran.
- El campo `agents` de la política (agentes compartidos) queda en desuso. No confundir con el switch **agents** del Policy Studio (subagentes/delegación), que no cambia.

## 9. Seguridad y riesgos

| Riesgo | Mitigación |
|---|---|
| Archivo de import malicioso | Parser estricto en servidor (§2) **y** en equipo (§6.1). Paquete canónico re-generado; nunca se sirve el archivo original. |
| Paquete alterado en tránsito (edge HTTP sin TLS) | sha256 dentro del bundle **firmado** (JWS); el equipo rechaza el paquete si no coincide. La confidencialidad del prompt queda sujeta al riesgo ya aceptado del piloto sin TLS. |
| MCP/cron conservados (D-045-C) | Visibles en la ficha antes de asignar. En runtime: P-6/P-9 más desactivar MCP no permitidos para que no arranque el proceso (§6.4). El admin que importa es la raíz de confianza, como quien instala software. |
| Skills con scripts | Solo se ejecutan con las tools de terminal gobernadas; la ficha marca "incluye scripts". |
| Secretos en el export | Exclusión de almacenes de credenciales y re-escaneo con redacción (§2); nada secreto se guarda ni se audita. |
| Aislamiento entre tenants | RLS en las tablas nuevas; el endpoint de paquete exige pertenencia al bundle del dispositivo (§5). |

## 10. Fuera de alcance (v1)

Mapear los secretos requeridos (`env_requires`) a credenciales del vault; importar distributions desde una URL git; catálogo curado por el proveedor más allá de los perfiles de fábrica; editar `SOUL.md` o skills desde la consola; compartir perfiles entre tenants.

## 11. Entrega por fases (cada fase con su verificación real)

1. **Plataforma**: migración (tablas y RLS), pipeline de import con sus tests (exports reales generados con el CLI de Hermes y archivos maliciosos), perfiles de fábrica y API del marketplace.
2. **Consola**: marketplace (grid, ficha, import, versiones, archivar) y sección Perfiles del editor de políticas con default. Verificación con login real por el navegador.
3. **Bundle, endpoint de paquete y edge**, con sus tests y la allowlist.
4. **Plugin**: instalar, actualizar, archivar, restaurar, default, MCP/cron; tests en Linux y en el Windows real (locks).
5. **Desktop**: reglas del selector y arranque en el default; build y publicación del MSIX.
6. **E2E sobre la app instalada** (VM, por la LAN y por el edge): AC-045-01…08.
