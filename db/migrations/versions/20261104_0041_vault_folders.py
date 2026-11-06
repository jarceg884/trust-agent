"""Migración 0041: carpetas del vault (spec Infisical-style, mandato Miller).

Agrega ``vault_folders`` (árbol anidado por owner, mismo modelo de
visibilidad que vault_credentials: org_admin ve todo el tenant, dept_admin
su depto, el resto solo lo propio) y la columna ``folder_id`` en
``vault_credentials`` para clasificar los secretos dentro de carpetas.
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_vault_folders"
down_revision = "0040_policy_contracts"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- 26. Carpetas del vault (árbol anidado, estilo Infisical) ------------------
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('vault.folder_created', 'Vault folder created (owner-scoped tree node).'),
  ('vault.folder_renamed', 'Vault folder renamed.'),
  ('vault.folder_deleted', 'Vault folder deleted (only when empty).'),
  ('vault.credential_moved', 'Vault secret moved to another folder (or root).')
ON CONFLICT (action) DO NOTHING;

CREATE TABLE IF NOT EXISTS vault_folders (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  user_id          uuid NOT NULL,
  parent_folder_id uuid,
  name             text NOT NULL CHECK (length(btrim(name)) > 0),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id),
  FOREIGN KEY (tenant_id, parent_folder_id) REFERENCES vault_folders (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS vault_folders_tenant_idx
  ON vault_folders (tenant_id, user_id, parent_folder_id);

DROP TRIGGER IF EXISTS vault_folders_set_updated_at ON vault_folders;
CREATE TRIGGER vault_folders_set_updated_at
  BEFORE UPDATE ON vault_folders FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE vault_credentials ADD COLUMN IF NOT EXISTS folder_id uuid;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'vault_credentials_folder_fk'
  ) THEN
    ALTER TABLE vault_credentials
      ADD CONSTRAINT vault_credentials_folder_fk
      FOREIGN KEY (tenant_id, folder_id) REFERENCES vault_folders (tenant_id, id);
  END IF;
END $$;

DO $$
DECLARE
  t text;
  tables text[] := ARRAY['vault_folders'];
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

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE vault_folders TO trust_app;
"""


def upgrade() -> None:
    op.execute(sa.text(SCHEMA_SQL))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE vault_credentials DROP CONSTRAINT IF EXISTS vault_credentials_folder_fk"))
    op.execute(sa.text("ALTER TABLE vault_credentials DROP COLUMN IF EXISTS folder_id"))
    op.execute(sa.text("DROP TABLE IF EXISTS vault_folders"))
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "('vault.folder_created','vault.folder_renamed',"
        " 'vault.folder_deleted','vault.credential_moved')"
    ))
