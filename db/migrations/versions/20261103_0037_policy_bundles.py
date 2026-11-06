"""Migración 0037: policy_bundles (Policy Studio, mandato Miller).

Una "política global" creada desde el Policy Studio: réplica de Settings de
Hermes Desktop (qué puede usar el usuario), credenciales del vault, prompt
global, perfiles predeterminados (perfil de agente + preset de modelo) y el
departamento al que se asigna.

El prompt global se inyecta desde el bundle firmado y NUNCA es editable desde
el desktop: la organización prevalece sobre SOUL.md y perfiles de usuario.
"""
from alembic import op

revision = "0037_policy_bundles"
down_revision = "0036_enrollment_token_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.policy_bundles (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          name          text NOT NULL,
          description   text NOT NULL DEFAULT '',
          desktop       jsonb NOT NULL DEFAULT '{}'::jsonb,
          credentials   jsonb NOT NULL DEFAULT '{}'::jsonb,
          global_prompt text NOT NULL DEFAULT '',
          profiles      jsonb NOT NULL DEFAULT '[]'::jsonb,
          department_id uuid,
          created_by    uuid,
          created_at    timestamptz NOT NULL DEFAULT now(),
          updated_at    timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, name)
        )
        """
    )
    op.execute("ALTER TABLE public.policy_bundles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.policy_bundles FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.policy_bundles
          FOR ALL TO trust_app
          USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON public.policy_bundles TO trust_app")
    op.execute("GRANT ALL ON public.policy_bundles TO trust_migrator")
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('policy_bundle.created', 'Policy Studio bundle created.'), "
        "  ('policy_bundle.updated', 'Policy Studio bundle updated.'), "
        "  ('policy_bundle.assigned', 'Policy Studio bundle assigned to a department.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.policy_bundles")
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action LIKE 'policy_bundle.%'"
    )
