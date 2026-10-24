# Plataforma SaaS de Gobernanza de IA sobre Hermes Agent

> **Nombre comercial: Trust Agent** (provisional, decidido por David). El placeholder "Harper" queda retirado.

## Visión

Aplicación que **gobierna agentes de IA a nivel organizacional**, construida sobre Hermes Agent, lista para operar como **SaaS**:

- El **proveedor** (nosotros) aprovisiona nuevos clientes de forma "a prueba de idiotas".
- Cada **cliente** tiene su panel de administración: organización, presupuesto, departamentos, accesos, políticas.
- Los usuarios finales **descargan el desktop white-label ("Trust Agent")** y ligan su dispositivo a la organización por defecto.
- Los **proveedores de IA (modelos) están controlados por el proveedor** — principalmente Huawei Cloud MaaS vía LiteLLM.
- Todo lo que un CIO preguntaría: gobernanza completa de IA (presupuestos, políticas, audit log, credenciales, dispositivos).

Referencia visual (mockups): Admin Console (Overview, Organization, Departments, Users & Roles, Budgets, Model Access, Shared Agents, Devices, Credentials, Audit Log, Branding) + Trust Agent desktop (sesiones, capacidades, agentes compartidos, artefactos, políticas activas, presupuesto departamental).

## Decisiones tomadas (Miller, 23-10-2026)

| # | Decisión | Elección |
|---|----------|----------|
| D1 | Base de construcción | **Plataforma nueva desde cero**; el proyecto AIDLC (zip) es SOLO referencia |
| D2 | Multi-tenancy | **Control plane compartido + aislamiento lógico** (tenant_id en DB, políticas por org) |
| D3 | Desktop | **White-label con branding del proveedor desde el inicio** (nombre del producto: Trust Agent (provisional)) |
| D4 | Modelos/facturación | **Ambos modos**: keys MaaS centralizadas del proveedor (cobra uso con margen) + BYOK del cliente |
| D5 | Infra | **Desarrollo local primero** (single-node en el servidor); **PostgreSQL** como DB — preparar el schema desde la auditoría |

## Alcance funcional (completo, no solo MVP)

1. **Onboarding de cliente (proveedor)**: aprovisionamiento de nuevo cliente idiom-proof — org, admin, branding, presupuesto inicial, políticas default.
2. **Admin Console del cliente**: organización, departamentos, usuarios & roles, presupuestos (org + por departamento + por usuario), model access (modelos aprobados), agentes compartidos (grants explícitos), dispositivos (enrolamiento/revocación), credentials, audit log, branding.
3. **Perfiles Hermes pre-diseñados por departamento** con skills: TI, cloud operaciones, y las soluciones más comunes de cada departamento.
4. **Desktop Trust Agent**: usuario descarga, se autentica (SSO+MFA), dispositivo queda ligado a la org; ve solo lo que las políticas permiten.
5. **Conectores a aplicaciones**: catálogo de conectores (VMware, email, mensajerías, etc.) que el usuario puede conectar — gobernado por políticas de la org (qué conectores permite cada departamento/usuario).
6. **Vault de credenciales de la app**: el usuario ingresa credenciales (VMware, emails, etc.) en un vault dentro de la app; credenciales scoped, cifradas, auditadas; la org define políticas de vault.
7. **Mensajerías**: gobernanza de a qué plataformas de mensajería puede conectar un usuario su agente (WhatsApp, Telegram, Slack, Teams, etc.).
8. **Gobernanza de modelos**: solo modelos aprobados por la org; routing vía LiteLLM; MaaS central + BYOK; pricing sync; presupuestos con corte.
9. **Audit log completo**: quién hizo qué, cuándo, desde qué dispositivo, con qué modelo, costo.
10. **Valor de la app / pitch**: gobernanza de IA enterprise sobre Hermes + MaaS.

## Reglas de ejecución (fase autónoma)

- **Nada simulado** — todo real y funcional.
- Español. USD para financieros. Cost-conscious.
- **Autonomía total**: si durante el desarrollo hace falta investigar (docs oficiales de Hermes, repos, LiteLLM, Keycloak, PostgreSQL, cualquier tecnología), investigar contra fuentes oficiales Y capturar el hallazgo como un **skill reutilizable** (en `/root/trust-agent/skills/` para compartir + en el skills dir del propio perfil). El equipo se vuelve más autónomo con cada tarea.
- Flujo: requisitos → schema PostgreSQL → arquitectura → código → **auditorías e2e iterativas hasta que todo sea funcional** → validación 100% de factores de gobernanza y funcionalidad.
- Kanban + software factory (orchestrator → @dev/@qa/@deploy) ejecutando autónomo.
- **No es un despliegue**: es desarrollo. El despliegue local es para validar, no el entregable final.

## Artefactos

- Código de referencia AIDLC: `/root/aidlc-audit/aidlc-bot-project/project/` (auditoría en curso: admin-panel, parches Hermes, infra K8s).
- Mockups: `/root/.hermes/images/upload_iaadt_lh.png` (Admin Console), `/root/.hermes/images/upload_lcw3pa1_.png` (Desktop).
- Auditoría AIDLC (3 reportes paralelos): pendiente de consolidar → informará el schema PostgreSQL y qué reutilizar como referencia.

## Pendientes

- [ ] Consolidar auditoría AIDLC (3 subagentes en curso)
- [ ] Requisitos formales (SDD/spec-kit style)
- [ ] Schema PostgreSQL (multi-tenant, orgs/departments/budgets/policies/devices/connectors/vault/audit)
- [ ] Arquitectura de la plataforma (control plane, enrolamiento desktop, LiteLLM, vault)
- [ ] Despachar kanban al software factory
