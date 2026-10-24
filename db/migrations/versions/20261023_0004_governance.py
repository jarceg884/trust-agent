"""Governance evolution (spec 005): shared agents, grants, notifications.

Revision ID: 0004_governance
Revises: 0003_devices_enrollment_token
Create Date: 2026-10-23

DDL canónico: db/schema.sql sección 9 (embebida verbatim aquí para que la
vía Alembic produzca el mismo schema — paridad exigida por
db/tests/test_schema_parity.py). 0001_initial queda congelado con el schema
de la spec 002. Apilada sobre 0002_login_throttle y
0003_devices_enrollment_token (tareas hermanas) para mantener una ÚNICA
cabeza de migración.
"""
from alembic import op

revision = "0004_governance"
down_revision = "0003_devices_enrollment_token"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- ============================================================================
-- 9. Evolución spec 005 (governance): agentes compartidos, grants, notificaciones
--     Misma migración: db/migrations/versions/20261023_0002_governance.py
--     (paridad pg_dump exigida por db/tests/test_schema_parity.py)
-- ============================================================================

-- 9.1 Nuevas acciones de audit (enum versionado, extensión por datos)
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('agent.shared',        'Agent published as shared (no grants by default).'),
  ('agent.unshared',      'Agent archived / unshared.'),
  ('agent.granted',       'Shared-agent grant created (invoke/view).'),
  ('agent.revoked',       'Shared-agent grant revoked.'),
  ('agent.invoked',       'Shared agent invoked (billed to the invoker).'),
  ('agent.invoke_denied', 'Shared-agent invocation denied (no invoke grant).'),
  ('model.key_issued',    'LiteLLM virtual key issued for a user (hash stored only).'),
  ('model.key_blocked',   'LiteLLM virtual key blocked (budget cutoff).'),
  ('model.key_unblocked', 'LiteLLM virtual key unblocked (budget recovered / period reset).'),
  ('budget.threshold',    'Budget soft threshold crossed (notification sent).')
ON CONFLICT (action) DO NOTHING;

-- 9.2 agents — agentes compartidos del tenant (REQ-AGT-001)
CREATE TABLE IF NOT EXISTS agents (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  owner_user_id uuid NOT NULL,
  department_id uuid,
  name          text NOT NULL,
  description   text,
  scope         text NOT NULL DEFAULT 'dept' CHECK (scope IN ('dept','org')),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, owner_user_id, name),
  FOREIGN KEY (tenant_id, owner_user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, department_id) REFERENCES departments (tenant_id, id)
);

-- 9.3 agent_grants — grants explícitos invoke/view (REQ-AGT-002)
CREATE TABLE IF NOT EXISTS agent_grants (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  agent_id        uuid NOT NULL,
  grantee_user_id uuid NOT NULL,
  permission      text NOT NULL CHECK (permission IN ('invoke','view')),
  granted_by      uuid,
  granted_at      timestamptz NOT NULL DEFAULT now(),
  revoked_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, agent_id, grantee_user_id, permission),
  FOREIGN KEY (tenant_id, agent_id) REFERENCES agents (tenant_id, id),
  FOREIGN KEY (tenant_id, grantee_user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, granted_by) REFERENCES users (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS agent_grants_grantee_idx ON agent_grants (tenant_id, grantee_user_id);

-- 9.4 notifications — inbox de gobernanza (cortes y umbrales; arq. §2.3)
CREATE TABLE IF NOT EXISTS notifications (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  user_id    uuid NOT NULL,
  type       text NOT NULL,
  payload    jsonb NOT NULL DEFAULT '{}',
  read_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS notifications_user_idx
  ON notifications (tenant_id, user_id, created_at DESC);

-- 9.5 Triggers updated_at para las tablas nuevas (la sección 6.1 corrió antes)
DROP TRIGGER IF EXISTS agents_set_updated_at ON agents;
CREATE TRIGGER agents_set_updated_at
  BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 9.6 RLS: mismo patrón fail-closed + FORCE que la sección 7
DO $$
DECLARE
  t text;
  tables text[] := ARRAY['agents','agent_grants','notifications'];
BEGIN
  FOREACH t IN ARRAY tables
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$
      CREATE POLICY tenant_isolation ON public.%I
        FOR ALL TO trust_app
        USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    $p$, t);
  END LOOP;
END $$;

-- 9.7 Grants explícitos a la app (idénticos a los de la sección 8)
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE agents, agent_grants, notifications TO trust_app;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    # Evolución forward-only en desarrollo local; el downgrade explícito se
    # añade al entrar a producción (constitution VII).
    raise NotImplementedError("forward-only in local development (spec 005)")
