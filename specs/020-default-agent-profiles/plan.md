# Plan técnico — Spec 020

## Cambios por capa

### 1. Datos (db/migrations + db/schema.sql)
- `db/migrations/versions/20261026_0009_default_agent_profiles.py` (rev `0009_default_agent_profiles`, sobre `0008_model_catalog_glm52`):
  - `ALTER TABLE agents ADD COLUMN profile_key text NULL`
  - `CREATE UNIQUE INDEX agents_tenant_profile_uidx ON agents (tenant_id, profile_key) WHERE profile_key IS NOT NULL AND status = 'active'`
  - `INSERT INTO catalog.audit_actions ... ('agent.profile_adopted', ...) ON CONFLICT DO NOTHING`
  - downgrade inverso (DROP INDEX, DROP COLUMN, DELETE de la acción).
- `db/schema.sql`: espejo idéntico (sección 9.2 columna + índice; seed de `agent.profile_adopted` en §3.5).

### 2. Backend (platform/app)
- **`app/governance/agent_profiles.py`** (nuevo): dataclass `AgentProfileTemplate` + `AGENT_PROFILE_TEMPLATES` (`analyst`, `coder`, `ops`) + servicio:
  - `list_profiles(conn, principal)` → catálogo + instancia adoptada del tenant (query por `profile_key`, RLS aísla tenant) + `effective_policy` del caller (`access.effective_models_for_user`, `effective_policy_for_user().allowed_connectors`, `budgets.status_for_user().cutoff`).
  - `adopt(conn, principal, key)` → validaciones (dept requerido, template existe), INSERT en `agents` con `profile_key`, captura del unique violation (`agents_tenant_profile_uidx` → 422 "already adopted"; `agents_owner_user_id_name_key` → 422 nombre duplicado), audit `agent.profile_adopted`.
- **`app/governance/agents.py`**: `invoke_gate` acepta `model_name: str | None`; orden del gate: grant (igual que hoy) → budget cutoff (403 `budget_cutoff` + audit `agent.invoke_denied` + commit + raise) → `assert_model_allowed` (403 `model_not_approved`, audit propio) → audit `agent.invoked` (payload con `model_name` cuando vino).
- **`app/errors.py`**: factory `budget_cutoff()` (403, reason `budget_cutoff`).
- **`app/governance/router.py`**: `GET /api/agent-profiles`, `POST /api/agent-profiles/{key}/adopt` (201), `InvokeBody {model_name: str | None}` en `POST /api/agents/{id}/invoke`.

### 3. Console (platform/console)
- `src/lib/types.ts`: `AgentProfileRow`, `AgentProfilesResponse`, `AdoptResult`, `InvokeResult`.
- `src/app/(console)/agents/agents-page.tsx`: card "Default profiles" (tabla + Adopt + dialog Effective policy) y dialog Invoke en la tabla de agentes (input de modelo, veredicto 200/403 visible).

### 4. Tests (platform/tests/test_agent_profiles.py, nuevo)
AC-020-01…06 contra PostgreSQL vivo (fixtures de conftest: mint real, RLS, audit verificado por SQL directo). Cobertura del 422 de doble adopción cross-usuario, aislamiento B, grants, invoke aprobado/no aprobado/compat, cutoff.

### 5. Evidencia real (stack local)
1. `pytest` completo en verde (suite existente + nueva).
2. `npm run build` del console en verde.
3. Reconstruir imágenes (`docker compose build` platform + console), `docker compose run --rm migrate` (aplica 0009 al stack), `--force-recreate` de api/workers/console.
4. Provisioning de tenant demo vía API del proveedor → login REAL en el navegador (PKCE S256 + enrolamiento TOTP real leyendo el secret del DOM) → Shared Agents → Default profiles → Effective policy → Adopt → Invoke con modelo aprobado (200) y no aprobado (403 visible). Capturas + verificaciones de audit por SQL.

## Riesgos
- **Unique violation parsing**: el INSERT de adopción puede chocar con 2 unique constraints distintos (perfil ya adoptado vs nombre duplicado del owner) — se distingue por nombre de constraint en el mensaje.
- **conn.commit() dentro del gate** (patrón D-CP-04 ya establecido en `assert_model_allowed` / `invoke_denied`): el audit de negación debe sobrevivir al rollback del error.
- **Paridad schema.sql ↔ migraciones** (AC-DB-040): ambas rutas deben crear lo mismo; el test de paridad existente lo cubre.
