# Contexto común para todas las tareas del board "trust-agent"

> Lee esto ANTES de empezar tu tarea. Es el contexto compartido del proyecto.

## Qué estamos construyendo

Una **plataforma SaaS de gobernanza de IA sobre Hermes Agent** (nombre del producto: **Trust Agent**, provisional):
- El **proveedor** aprovisiona clientes (tenants) de forma idiom-proof.
- Cada cliente tiene: organización, departamentos, usuarios/roles, presupuestos jerárquicos (org→depto→usuario), políticas, model access (modelos aprobados vía LiteLLM hacia Huawei MaaS, modo central + BYOK), agentes compartidos con grants explícitos, dispositivos enrolados, conectores a aplicaciones (VMware, email, mensajerías...), vault de credenciales cifrado, audit log inmutable, branding white-label.
- El usuario final descarga un **desktop white-label** y liga su dispositivo a la organización; solo ve lo que las políticas le permiten.
- **Multi-tenant con aislamiento lógico**: control plane compartido, `tenant_id` en todo, PostgreSQL con RLS.
- **Desarrollo local primero** (este servidor); NO es un despliegue productivo todavía.

## Documentos fuente (léelos según tu tarea)

| Doc | Contenido |
|-----|-----------|
| `/root/trust-agent/DECISIONES.md` | Visión, decisiones D1-D5, alcance funcional completo, reglas |
| `/root/trust-agent/auditoria/AUDITORIA-AIDLC.md` | Auditoría del proyecto de referencia: hallazgos críticos (C1-C6), patrones reutilizables, implicaciones de diseño |
| `/root/.hermes/images/upload_iaadt_lh.png` | Mockup Admin Console (OCR: `tesseract <file> -`) |
| `/root/.hermes/images/upload_lcw3pa1_.png` | Mockup Desktop (OCR: `tesseract <file> -`) |
| `/root/aidlc-audit/aidlc-bot-project/project/` | Código AIDLC de referencia (SOLO referencia — NO copiar su modelo single-tenant) |

## Herramientas autorizadas

- **Spec Kit (SDD, OBLIGATORIO)**: el proyecto está inicializado con specify-cli (`specify` en PATH, constitution en `/root/trust-agent/.specify/memory/constitution.md`). Flujo por feature: `specify → plan → tasks → implement → converge`. Nada se implementa sin spec en `/root/trust-agent/specs/NNN-<nombre>/`. Una tarea solo se completa cuando converge (implementación == spec, checks de aceptación pasan con evidencia real). Lee el constitution antes de empezar.
- **REA** (https://github.com/morluto/rea): MCP de reverse engineering (binarios, apps, sitios, APKs). **Solo si es necesario** — último recurso cuando la documentación oficial no basta.
- **UI**: Next.js (App Router) + TypeScript + Tailwind. Estándar ultra profesional — cada pantalla a nivel producto enterprise.

## Reglas del equipo

1. **Nada simulado** — todo real y funcional. Nada de datos inventados presentados como resultados.
2. **Español** para docs, comentarios y reportes. USD para montos.
3. **Autonomía**: si te falta conocimiento, **investiga contra docs oficiales** (hermes-agent.nousresearch.com/docs, docs oficiales de LiteLLM/Keycloak/PostgreSQL, repos GitHub) y **captura el hallazgo como un skill reutilible** en `/root/trust-agent/skills/<categoría>/<nombre>/SKILL.md` (formato YAML frontmatter + markdown, mira ejemplos en /root/.hermes/skills/). Los skills hacen al equipo más autónomo.
4. **Entregables SIEMPRE en rutas absolutas bajo `/root/trust-agent/`** (docs/, db/, platform/, skills/). Tu workspace de tarea es scratch — lo importante vive en /root/trust-agent/.
5. **Cero parches al core de Hermes**: la integración con Hermes Agent se hace vía plugins oficiales/configuración. Lección C6/fragilidad de la auditoría: la sobrescritura de archivos core es prohibitiva.
6. **Seguridad por diseño**: nada de secretos en respuestas de API (one-time), sesiones/jobs en DB (nunca en memoria), JWT con verificación de firma (JWKS), auth authorization-code+PKCE (ROPC solo para bootstrap interno si es imprescindible), audit log inmutable.
7. Al terminar: `kanban_complete()` con resumen + archivos creados + cómo verificarlo. Si te faltan decisiones humanas: `kanban_block()` con la pregunta específica.
8. Si encuentras fallos de OTRO perfil en código ya completado: crea una tarea nueva con `hermes kanban create --assignee <perfil> --parent <esta-tarea>` describiendo el fallo con archivo:línea, y menciónalo en tu resumen.
9. **Migraciones Alembic** (`db/migrations/versions/`, hotspot): anuncia en un comentario de tu card ANTES de crear el archivo y créala SIEMPRE con `python3 db/scripts/migration_guard.py new --slug <slug>` (lock de serialización que apila sobre el head real — nunca escribas el archivo a mano); `down_revision` = head actual; `revision` ≤ 32 chars; paridad con `db/schema.sql`; y al terminar **reconstruye la imagen** (`docker compose build migrate && docker compose up -d`) — el servicio `migrate` corre desde la imagen build-eada, no desde el árbol, y ahora valida una única cabeza antes del upgrade. Convención completa en `db/README.md` § "Convención de serialización" y ADR `docs/adr-0001-serializacion-migraciones-alembic.md`.
10. **NUNCA `git stash -u` ni `git clean -fd` en el árbol compartido** (`/root/trust-agent`): borran directorios no-versionados que son fuente de bind mounts de Docker en vivo (p. ej. el tema white-label `platform/idp-themes/`, montado en `idp`). Al recrearse el dir cambia el inode y el mount del contenedor queda stale/vacío **sin error alguno** — Keycloak sirve el tema stock (regresión silenciosa de AC-AUTH-001, t_689fda26). Los assets compartidos críticos ya están trackeados en git; si creas uno nuevo que alimente un bind mount, **commitealo**. El healthcheck de `idp` ahora detecta el mount stale (contenedor `unhealthy`); si lo ves así, `docker compose restart idp`.
