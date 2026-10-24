#!/usr/bin/env python3
"""Crea el grafo de tareas del board kanban 'harper' (idempotente)."""
import json, subprocess, sys

CTX = "Lee primero /root/trust-agent/CONTEXTO-TAREAS.md y el constitution /root/trust-agent/.specify/memory/constitution.md. "

TASKS = [
    # key, title, assignee, priority, parents, goal, goal_turns, max_runtime, body
    ("t01", "Spec maestra de la plataforma (SDD specify)", "product-owner", 1, [], True, 25, "2h",
     CTX + "Ejecuta la fase specify del SDD para TODA la plataforma. Entregable: /root/trust-agent/specs/001-platform/spec.md con requisitos EARS (REQ-XXX) cubriendo: onboarding de tenant por el proveedor, organización/departamentos/usuarios/roles, presupuestos jerárquicos org→depto→usuario con corte, políticas (depto e individuales), model access (modelos aprobados vía LiteLLM, MaaS central + BYOK), agentes compartidos con grants explícitos, dispositivos enrolados, conectores a aplicaciones (VMware, email, mensajerías WhatsApp/Telegram/Slack/Teams), vault de credenciales, audit log inmutable, branding white-label, desktop enrolado a la org, y perfiles Hermes pre-diseñados por departamento (TI, cloud ops, finanzas, customer ops). Incluye user stories por rol (proveedor, admin de cliente, depto admin, usuario final) y criterios de aceptación verificables. Usa los mockups (OCR tesseract) y la auditoría como fuente. Si te falta conocimiento de dominio, investiga docs oficiales y crea skills."),

    ("t02", "Investigación técnica + skills base", "dev", 1, [], True, 25, "2h",
     CTX + "Investiga contra docs oficiales y crea un skill por tema en /root/trust-agent/skills/ (formato SKILL.md con YAML frontmatter, ejemplos en /root/.hermes/skills/): (1) Hermes Agent plugins/extensión — hermes-agent.nousresearch.com/docs y repo: cómo registrar plugins/providers SIN tocar core (el ecosistema register_provider del dashboard-auth), capacidades de profiles/skills del CLI; (2) LiteLLM proxy API — virtual keys, budgets/max_budget/budget_duration, teams, spend endpoints, pricing update; (3) Keycloak Admin REST + OIDC authorization-code+PKCE + JWKS verification; (4) PostgreSQL RLS multi-tenant (tenant_id, policies, session variables). Además escribe /root/trust-agent/docs/investigacion.md con el resumen ejecutivo y pitfalls de cada tema. Cada skill debe tener comandos/APIs concretos probados o citados con fuente."),

    ("t03", "Schema PostgreSQL multi-tenant (SDD)", "dev", 2, ["t01", "t02"], False, None, "2h",
     CTX + "Fase SDD completa para la capa de datos: crea /root/trust-agent/specs/002-data-layer/ (spec.md, plan.md, tasks.md) y luego implementa. Entregables: /root/trust-agent/db/schema.sql (DDL completo) + proyecto Alembic en /root/trust-agent/db/migrations/ + /root/trust-agent/db/README.md. Tablas: tenants, departments, users, roles/permissions (RBAC), budgets (jerárquicos org→depto→usuario con corte y período), policies (org/depto/usuario), devices, connectors (catálogo + instancias por usuario), vault_credentials (cifrado), audit_log (append-only + hash_prev), model_access (modelos aprobados, MaaS central + BYOK), api_keys, sessions, jobs. tenant_id en TODO + políticas RLS. Incluye tests de aislamiento (un tenant no ve otro) en /root/trust-agent/db/tests/. Converge antes de completar."),

    ("t04", "Arquitectura de la plataforma + ADRs", "dev", 2, ["t01", "t02"], False, None, "2h",
     CTX + "Entregable: /root/trust-agent/docs/arquitectura.md. Componentes: control plane API (FastAPI), Admin Console (Next.js), enrolamiento desktop, LiteLLM como plano de control de modelos, IdP OIDC, vault, audit. ADRs de: D1-D5 de DECISIONES.md, lecciones C1-C6 de la auditoría (por qué NO hacer lo que hizo AIDLC), integración con Hermes Agent EXCLUSIVAMENTE vía plugins/config oficiales (cero parches de core — cita el mecanismo register_provider), flujo de enrolamiento de dispositivo de primera clase, presupuesto jerárquico con agregación de spend. Incluye diagrama (mermaid) y contratos entre componentes."),

    ("t05", "Control plane core: API + auth + multi-tenant + RBAC", "dev", 3, ["t03", "t04"], True, 30, "4h",
     CTX + "Fase SDD (spec 003-control-plane) e implementación del núcleo en /root/trust-agent/platform/: proyecto Python (pyproject, FastAPI, SQLAlchemy sobre el schema de t03, Alembic). Auth OIDC authorization-code + PKCE con verificación JWKS (firma/exp/aud/iss) — NADA de ROPC para usuarios finales ni JWT sin verificar (lecciones C2/C6). Middleware multi-tenant: tenant_id en cada query vía RLS. RBAC: org admin / depto admin / usuario. Sesiones y jobs en PostgreSQL — CERO estado en memoria (lección C2). Estructura: app/ (main, auth, tenants, governance, vault, devices, audit, connectors), tests/ unitarios por módulo. Todo corre local con PostgreSQL. Converge: tests pasan, checklist del constitution verificado."),

    ("t06", "Onboarding de tenant idiom-proof", "dev", 4, ["t05"], False, None, "3h",
     CTX + "Fase SDD (spec 004-tenant-onboarding) e implementación: aprovisionamiento de un nuevo cliente por el proveedor — CLI (harper-provision) + endpoint admin. Crea todo el tenant: org, admin inicial (entrega de credenciales one-time), branding, presupuesto inicial, políticas default, departamentos seed con perfiles Hermes pre-diseñados (skills por departamento: TI, cloud ops, etc.), catálogo de modelos aprobado inicial. Rollback transaccional total si algo falla a mitad. Verificación post-alta automatizada (healthcheck del tenant nuevo). Tests e2e del flujo completo. Converge antes de completar."),

    ("t07", "Gobernanza: budgets jerárquicos + políticas + model access", "dev", 4, ["t05"], False, None, "3h",
     CTX + "Fase SDD (spec 005-governance) e implementación: (1) presupuestos jerárquicos org→depto→usuario con agregación de spend desde LiteLLM y corte con notificación cuando se agota; (2) políticas por org/depto/usuario (qué conectores, qué modelos, qué mensajerías, SSO+MFA requerido); (3) model access: modelos aprobados por org, integración LiteLLM (virtual keys por usuario con max_budget), modo MaaS central + BYOK; (4) agentes compartidos con grants explícitos (own/invoke/view). Tests de cada regla de gobernanza. Converge antes de completar."),

    ("t08", "Vault de credenciales + conectores + mensajerías", "dev", 4, ["t05"], False, None, "3h",
     CTX + "Fase SDD (spec 006-vault-connectors) e implementación: vault cifrado en reposo (envelope encryption con clave maestra del servidor de desarrollo), entrega de secretos one-time (jamás en respuesta persistente — lección C1/C4), scoped por conector. Catálogo de conectores: VMware, email (IMAP/SMTP), mensajerías (WhatsApp, Telegram, Slack, Teams), y estructura extensible. Gobernanza: qué conectores puede activar cada depto/usuario según políticas (integración con t07). Todo acceso al vault queda en audit_log. Tests. Converge antes de completar."),

    ("t09", "Enrolamiento de dispositivos", "dev", 4, ["t05"], False, None, "3h",
     CTX + "Fase SDD (spec 007-devices) e implementación: flujo de primera clase (NADA de scraping — lección C5): el desktop genera un device keypair, solicita enrolamiento con un código de la org, el admin aprueba desde la consola, el servidor liga dispositivo↔usuario↔org y emite device token firmado. Revocación de dispositivo (invalida token inmediatamente). Estado por dispositivo (aprobado/pendiente/revocado, último seen). Integración con audit_log. Tests del flujo completo. Converge antes de completar."),

    ("t10", "Audit log inmutable", "dev", 4, ["t05"], False, None, "2h",
     CTX + "Fase SDD (spec 008-audit) e implementación: tabla append-only con hash chain (cada fila lleva hash_prev + hash propio) — manipulación detectable. Eventos de TODA la plataforma: auth (login/token), provisioning, cambios de política, accesos al vault, spend/corte de presupuesto, enrolamiento/revocación de dispositivos, grants de agentes. API de consulta filtrable (por tenant, usuario, tipo, rango de tiempo) respetando RLS. Verificador de cadena como herramienta CLI. Tests incl. intento de manipulación detectado. Converge antes de completar."),

    ("t11", "Converge del core: e2e + auditoría de seguridad", "qa", 5, ["t06", "t07", "t08", "t09", "t10"], True, 30, "4h",
     CTX + "Eres QA: audita TODO el control plane (t05-t10) contra las specs. (1) Suite e2e en /root/trust-agent/platform/tests/e2e/: flujo completo real — alta de tenant → departamentos → usuarios → políticas → presupuesto → uso de modelo (LiteLLM real o stub declarado honesto) → corte → enrolamiento de dispositivo → conector → audit verificado. (2) Checklist de seguridad: verifica que NINGÚN hallazgo de la auditoría AIDLC (C1-C6 y los altos) se repite — password en respuestas, sesiones en memoria, JWT sin firma, verify=False, secretos en claro. (3) Tests de aislamiento multi-tenant (tenant A no ve tenant B, ni por API ni por DB). Ejecuta TODO, documenta evidencia real. Fallos menores: arréglalos tú con test que lo pruebe. Fallos de diseño: crea tarea dev con 'hermes kanban create --assignee dev --parent <tu-tarea>' con archivo:línea y desbloquea al resolverse. Itera hasta que TODO pase. Reporte: /root/trust-agent/docs/qa-core.md con evidencia."),

    ("t12", "Admin Console Next.js ultra profesional", "ui", 6, ["t11"], True, 30, "4h",
     CTX + "Fase SDD (spec 009-admin-console) e implementación en /root/trust-agent/platform/console/ (Next.js App Router + TypeScript + Tailwind). Páginas: Overview (presupuesto org usado/restante, departamentos), Organization, Departments (budgets por depto), Users & Roles, Budgets, Model Access (modelos aprobados), Shared Agents (grants), Devices (enrolamiento/revocación), Credentials (vault), Audit Log (filtrable), Branding (white-label). Referencia visual: mockups (OCR tesseract). Integración REAL con la API del control plane — tipos generados/derivados del schema, estados de carga/error reales, CERO datos mock presentados como reales. Estándar ultra profesional enterprise: tipografía, spacing, estados vacíos, accesibilidad, responsive. 'npm run build' limpio antes de completar. Converge contra la spec."),

    ("t13", "Desktop: flujo de enrolamiento white-label", "ui", 6, ["t09", "t11"], False, None, "3h",
     CTX + "Fase SDD (spec 010-desktop-enrollment) e implementación: la experiencia del usuario final que descargó el desktop. (1) Documento /root/trust-agent/docs/desktop-flow.md: flujo completo login OIDC/PKCE → solicitud de enrolamiento de dispositivo → aprobación → recepción de políticas/perfiles/skills de la org → sesión gobernada (solo modelos aprobados, presupuesto visible, políticas activas visibles — como el mockup). (2) Implementación del lado cliente en /root/trust-agent/platform/desktop-client/: pantalla de login + enrolamiento + dashboard mínimo del usuario (presupuesto, políticas, dispositivos), integrada con la API real (t09). (3) Cómo se integra con Hermes Agent vía configuración/plugins oficiales (usa el skill de t02) — el desktop es Hermes configurado, NO un fork parcheado. 'npm run build' limpio. Converge antes de completar."),

    ("t14", "Validación final 100%: gobernanza + funcionalidad", "qa", 7, ["t12", "t13"], True, 30, "4h",
     CTX + "Validación final de la plataforma completa. (1) E2E global: proveedor crea tenant → admin configura org (deptos, presupuestos, políticas, modelos) → usuario se enrola desde desktop → usa un agente con modelo aprobado → presupuesto se descuenta → corte → audit log refleja TODO → revocación de dispositivo bloquea acceso. (2) Checklist CIO: cada ítem de gobernanza del spec 001 verificado con evidencia real (comando/test/screenshot-path). (3) Verificación del constitution: los 8 principios, uno por uno. (4) Reporte final /root/trust-agent/docs/validacion-final.md: qué funciona (con evidencia), qué no, riesgos, y demo script paso a paso. Fallos: arréglalos si son menores o crea tareas dev vinculadas para los de diseño. NO completes hasta que el checklist esté 100% o los pendientes estén documentados como tareas abiertas con severidad."),

    ("t15", "Empaquetado local single-node", "deploy", 8, ["t14"], False, None, "2h",
     CTX + "Empaqueta para validación local: /root/trust-agent/docker-compose.yml (PostgreSQL, control plane, console, LiteLLM, IdP si aplica) + /root/trust-agent/README.md con arranque paso a paso + verificación de salud. Levántalo REALMENTE en este servidor, corre el flujo de un tenant demo de punta a punta (provision → admin → usuario) y documenta la evidencia en /root/trust-agent/docs/smoke-local.md. Si algo no levanta, arréglalo o crea tarea dev vinculada. Nada de declarar 'debería funcionar' — evidencia real o bloqueado."),
]

def run(args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("FAIL:", " ".join(args), file=sys.stderr)
        print(r.stderr[-800:], file=sys.stderr)
        sys.exit(1)
    return r.stdout

ids = {}
for key, title, assignee, prio, parents, goal, turns, runtime, body in TASKS:
    args = ["hermes", "kanban", "create", title,
            "--assignee", assignee,
            "--priority", str(prio),
            "--body", body,
            "--idempotency-key", f"harper-{key}",
            "--max-runtime", runtime,
            "--json"]
    for p in parents:
        args += ["--parent", ids[p]]
    if goal:
        args += ["--goal", "--goal-max-turns", str(turns)]
    out = run(args)
    d = json.loads(out)
    ids[key] = d.get("task_id") or d.get("id")
    print(f"{key} -> {ids[key]}  [{assignee}] {title}")

print("\nTareas creadas:", len(ids))
