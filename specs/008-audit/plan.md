# Plan técnico — Spec 008 Audit

## 1. Contexto físico

- **Ya existe (spec 002, no se toca)**: `audit_log` (append-only, `seq`/`hash_prev`/`hash`
  por trigger + advisory lock), trigger de rechazo de UPDATE/DELETE, `audit_verify_chain(tenant)`,
  RLS `tenant_isolation`, grants INSERT-only para `trust_app`, catálogo `catalog.audit_actions`.
- **Ya existe (spec 003, no se rompe)**: `app/audit/service.py` (`emit`, `verify_chain`),
  `app/audit/router.py` (`POST /api/admin/audit/verify`, org_admin), `tenant_tx`/`set_tenant`
  en `app/db.py`, RBAC `require_permission` en `app/auth/rbac.py`, envelope de errores.

## 2. Arquitectura de la solución

```
app/audit/
├── service.py   # emit (existe) + verify_chain (existe) + query_events + export + scope por rol
├── router.py    # POST /api/admin/audit/verify (existe, + audit.verified)
│                # GET  /api/audit/events          (nuevo, audit.view + scope)
│                # GET  /api/audit/events/export   (nuevo, csv|json)
└── cli.py       # python -m app.audit.cli  ==  console script `verify-audit-chain`
```

- **Consulta**: una sola query parametrizada sobre `audit_log a LEFT JOIN users u`
  (u = departamento del actor para scope de dept_admin y filtro `department_id`).
  `WHERE` compuesto dinámicamente con binds nombrados; `ORDER BY a.id DESC`;
  `total` = `count(*)` con el mismo FROM/WHERE. Scope por rol (D-AU-02):
  - org_admin → `TRUE`
  - dept_admin → `u.department_id = ANY(:depts)`
  - user → `a.actor_id = CAST(:uid AS uuid)`
- **Export**: misma query sin paginar con tope 10 000; CSV via `csv.writer` +
  `StreamingResponse` (columnas fijas, incl. hashes); JSON via `JSONResponse`.
- **CLI**: `argparse` con subcomando `verify`; `verify <tenant_id> | verify --all`.
  Verificación como `trust_app` + `set_config` (scope por tenant); listado de tenants
  para `--all` con engine `trust_migrator` SOLO para el SELECT de ids (patrón D-CP-10).
  Emite `audit.verified` con `source="cli"` (D-AU-05). Exit 0/1/2 (D-AU-06).
- **Instrumentación auth** (`app/auth/router.py`, `app/auth/service.py`):
  - mint exitoso → `auth.login` (rol de mayor precedencia via `load_roles`).
  - mint fallido post-tenant (usuario desconocido/disabled) → `auth.login_failed` +
    `conn.commit()` explícito antes del `raise invalid_token()` (D-AU-03/07).
  - logout → `auth.logout`.
  - refresh replay → `auth.login_failed {reason: refresh_reuse}` dentro de la
    transacción de revocación, commit antes del 401 (D-AU-04).
- **Instrumentación jobs** (`app/jobs/service.py`): `job.created` en `enqueue` (solo
  INSERT nuevo), `job.completed` en `complete`; `actor_role="system"`,
  `actor_id` opcional en `enqueue`.

## 3. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Emit dentro de transacción que luego hace rollback por 401 → evento perdido | Commit explícito antes del raise en los caminos de fallo auditado (patrón ya probado en `rotate_refresh`). |
| Wildcards de usuario en `q` (`%`/`_`) rompan la búsqueda literal | Escape de `%`, `_`, `\` en el bind. |
| Conteo/`total` inconsistente con items por RLS | Ambos en la MISMA transacción scoped (`get_tx`). |
| Colisión con specs 004–007 (corren en paralelo) | Esta spec NO toca `tenants/`, `governance/`, `vault/`, `devices/`, `connectors/`, `models/`. Contrato dejado en comentarios de sus cards. |
| Cadena rota por tests de tamper contamine otras suites | Tenant dedicado (creado por el test con superuser de test), nunca A/B. |
| `require_permission("audit.view")` dependa de constantes | El permiso se lee del catálogo (data-driven, I10); los 3 roles lo tienen sembrado. |

## 4. Orden de implementación (tasks.md)

1. `service.query_events` + scope + export (sin ruta).
2. Rutas `GET /api/audit/events` y `/export`; extender `POST verify` con `audit.verified`.
3. CLI `app/audit/cli.py` + console script `verify-audit-chain` en `pyproject.toml`.
4. Instrumentación auth (login/login_failed/logout/refresh_reuse).
5. Instrumentación jobs (created/completed).
6. Tests: consulta+scope+filtros+paginación, export, CLI (subprocess, exit codes),
   tamper UPDATE/DELETE (API+CLI), eventos auth/jobs, REQ-AU-031 (acciones de dominio).
7. Converge: suite completa verde + evidencia.
