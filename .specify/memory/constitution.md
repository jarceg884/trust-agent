# Plataforma SaaS de Gobernanza de IA (trust-agent) — Constitution

## Core Principles

### I. Nada Simulado (NO NEGOCIABLE)
Todo dato, resultado y demo debe ser real y verificable. Prohibido presentar datos inventados, mocks hardcodeados o salidas sintéticas como resultados reales. Si algo no se puede verificar con evidencia (comando ejecutado, test pasado, archivo con hash), no está hecho.

### II. Spec-Driven (NO NEGOCIABLE)
Nada se implementa sin spec: `specify → plan → tasks → implement → converge`. Cada feature vive en `specs/NNN-<nombre>/` con spec.md, plan.md y tasks.md. Una tarea solo se marca completa cuando converge: la implementación coincide con la spec y los checks de aceptación pasan contra evidencia real. Si la spec y el código divergen, se corrige el código (o se documenta el cambio de spec con razón explícita), nunca se "ajusta" el check.

### III. Multi-Tenant con Aislamiento Lógico
Control plane compartido; `tenant_id` en cada tabla y cada query; PostgreSQL Row Level Security como defensa en profundidad. Ningún endpoint, job o query accede datos sin scope de tenant. Un admin de un tenant jamás ve datos de otro — verificable con tests de aislamiento.

### IV. Seguridad por Diseño
Auth: authorization code + PKCE, verificación JWKS de firma/exp/aud/iss. Secretos: cifrado en reposo, entrega one-time, jamás en cuerpos de respuesta persistentes ni logs. Sesiones y jobs en PostgreSQL, nunca en memoria de proceso. Audit log append-only con hash chain. Presupuestos con corte y jerarquía org→depto→usuario. Prohibido: ROPC para usuarios finales, `verify=False`, parches al core de Hermes (integración solo vía plugins/config oficiales).

### V. Gobernanza Completa (el producto ES la gobernanza)
Cada capacidad de la plataforma debe ser gobernable: quién puede usarla, con qué presupuesto, con qué políticas, auditada. Si una feature no tiene historia de gobernanza (policy, budget, audit, acceso), está incompleta.

### VI. Stack Fijo
Backend: Python + FastAPI + PostgreSQL (SQLAlchemy + Alembic). Frontend: Next.js (App Router) + TypeScript + Tailwind CSS, estándar ultra profesional. Modelos: LiteLLM como plano de control (MaaS central + BYOK). Identidad: OIDC (Keycloak o equivalente). Docs y comentarios en español; UI copy en inglés; montos en USD.

### VII. Desarrollo Local Primero
Todo corre y se valida en este servidor (single-node). Nada de despliegues productivos ni infra cloud nueva sin autorización explícita del humano. El entregable es código + evidencia, no infraestructura.

### VIII. Autonomía con Skills
Si falta conocimiento, se investiga contra docs oficiales y el hallazgo se captura como skill reutilizable en `/root/trust-agent/skills/`. Reverse engineering (REA) solo como último recurso. Cada tarea deja al equipo más autónomo, no solo el problema resuelto.

## Language & Workflow Constraints

- Specs, planes, tareas, docs, comentarios y reportes: **español**.
- UI copy y nombres de código: **inglés**.
- Commits: convencionales en inglés (`feat:`, `fix:`, `test:`).
- Montos monetarios: **USD**.

## Development Workflow (SDD)

1. `/speckit-specify` — spec de la feature en `specs/`
2. `/speckit-clarify` (si hay ambigüedad) — desambiguar; con el humano solo si bloquea
3. `/speckit-plan` — plan técnico
4. `/speckit-tasks` — tareas accionables
5. `/speckit-implement` — implementar
6. `/speckit-converge` — verificar convergencia; repetir 5-6 hasta "Converged"

## Governance

El constitution prima sobre cualquier otra práctica. Toda tarea del board kanban "trust-agent" debe verificar cumplimiento antes de completarse. Cambios al constitution requieren razón documentada y aprobación del humano. Referencia operativa: `/root/trust-agent/CONTEXTO-TAREAS.md` y `/root/trust-agent/DECISIONES.md`.

**Version**: 1.0.0 | **Ratified**: 2026-10-23 | **Last Amended**: 2026-10-23
