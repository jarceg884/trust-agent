"""Migración 0048: marketplace de perfiles de agente (spec 045).

``agent_profiles`` es el catálogo por tenant (perfiles importados de Hermes y los de fábrica
Analyst/Coder/Ops) y ``agent_profile_versions`` sus versiones INMUTABLES: cada import guarda un
paquete saneado direccionado por contenido (sha256) — la versión vigente es la más alta
(D-045-E). ``trust_app`` no puede borrar perfiles (se archivan, D-045-03) ni tocar versiones.

``policy_bundles.default_profile_id`` es el perfil por defecto de la política (D-045-04); sus
``profiles`` pasan a ser ``[{profile_id, model}]``. Las entradas ``{name, model}`` de la spec 044
no se tocan: no se pueden mapear a un perfil del marketplace sin decisión del admin, ningún
cliente las consumía, y el API rechaza guardar la política hasta que se elijan del marketplace.
En el despliegue piloto no existía ninguna al migrar.

Los triggers de ``bump_policy_revision`` (migración 0047) cubren también ambas tablas: importar una
versión nueva o archivar un perfil sube la revisión del tenant, así el heartbeat anuncia
``policy_update_available`` y los equipos bajan el bundle con el sha256 nuevo en el siguiente
sync (D-045-E) sin que ninguna política cambie.
"""
from alembic import op
import sqlalchemy as sa

revision = "0048_agent_profiles"
down_revision = "0047_policy_revision"
branch_labels = None
depends_on = None

_REVISION_TABLES = ("agent_profiles", "agent_profile_versions")

SCHEMA_SQL = r"""
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('agent_profile.imported', 'Marketplace profile imported (sanitized Hermes profile package).'),
  ('agent_profile.version_added', 'New version of a marketplace profile imported.'),
  ('agent_profile.updated', 'Marketplace profile name or description edited.'),
  ('agent_profile.archived', 'Marketplace profile archived.'),
  ('agent_profile.restored', 'Marketplace profile restored from the archive.'),
  ('agent_profile.factory_seeded', 'Factory profile added to the tenant marketplace.')
ON CONFLICT (action) DO NOTHING;

CREATE TABLE IF NOT EXISTS agent_profiles (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  slug         text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
  name         text NOT NULL CHECK (length(btrim(name)) > 0),
  description  text NOT NULL DEFAULT '',
  source       text NOT NULL CHECK (source IN ('imported', 'factory')),
  factory_key  text,
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, slug),
  CHECK ((source = 'factory') = (factory_key IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_profiles_factory_key
  ON agent_profiles (tenant_id, factory_key) WHERE factory_key IS NOT NULL;

DROP TRIGGER IF EXISTS agent_profiles_set_updated_at ON agent_profiles;
CREATE TRIGGER agent_profiles_set_updated_at
  BEFORE UPDATE ON agent_profiles FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS agent_profile_versions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL,
  profile_id   uuid NOT NULL,
  version      integer NOT NULL CHECK (version >= 1),
  sha256       text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size         bigint NOT NULL CHECK (size > 0),
  manifest     jsonb NOT NULL DEFAULT '{}'::jsonb,
  imported_by  uuid,
  imported_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (profile_id, version),
  FOREIGN KEY (tenant_id, profile_id) REFERENCES agent_profiles (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS agent_profile_versions_latest_idx
  ON agent_profile_versions (tenant_id, profile_id, version DESC);

ALTER TABLE policy_bundles ADD COLUMN IF NOT EXISTS default_profile_id uuid;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'policy_bundles_default_profile_fk'
  ) THEN
    ALTER TABLE policy_bundles
      ADD CONSTRAINT policy_bundles_default_profile_fk
      FOREIGN KEY (tenant_id, default_profile_id) REFERENCES agent_profiles (tenant_id, id)
      ON DELETE SET NULL (default_profile_id);
  END IF;
END $$;

DO $$
DECLARE
  t text;
  tables text[] := ARRAY['agent_profiles', 'agent_profile_versions'];
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

-- Perfiles: se archivan, nunca se borran (D-045-03). Versiones: inmutables.
GRANT SELECT, INSERT, UPDATE ON TABLE agent_profiles TO trust_app;
GRANT SELECT, INSERT ON TABLE agent_profile_versions TO trust_app;
GRANT ALL ON TABLE agent_profiles, agent_profile_versions TO trust_migrator;
"""


def upgrade() -> None:
    op.execute(sa.text(SCHEMA_SQL))
    for table in _REVISION_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_bump_policy_revision "
            f"AFTER INSERT OR UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.bump_policy_revision()"
        )


def downgrade() -> None:
    for table in _REVISION_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_bump_policy_revision ON public.{table}")
    op.execute(
        "ALTER TABLE policy_bundles DROP CONSTRAINT IF EXISTS policy_bundles_default_profile_fk"
    )
    op.execute("ALTER TABLE policy_bundles DROP COLUMN IF EXISTS default_profile_id")
    op.execute("DROP TABLE IF EXISTS agent_profile_versions")
    op.execute("DROP TABLE IF EXISTS agent_profiles")
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action LIKE 'agent_profile.%'"
    )
