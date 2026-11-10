"""Migración 0047: revisión de contenido de la política, por tenant.

``policy_version`` se calculaba solo con ``policies.version`` (la capa
técnica). Policy Studio (``policy_bundles``) y Model Access (``model_access``)
no versionan por fila, así que sus cambios llegaban al dispositivo con la
MISMA versión: el agente reportaba "policy v1, sin cambios", el heartbeat no
anunciaba ``policy_update_available`` y la attestation no probaba qué
contenido había aplicado el dispositivo (pilot e2e-lab, 2026-11-10).

``policy_revisions`` lleva un contador monótono por tenant que suben triggers
AFTER INSERT/UPDATE/DELETE sobre ambas tablas: cuenta CUALQUIER vía de
escritura (API de la consola, aprovisionamiento, SQL manual). El compilador
publica ``policy_version`` = máx versión activa de ``policies`` + revisión del
tenant. La revisión arranca en 0: ningún dispositivo ve un cambio de versión
por el despliegue en sí.

Sin SECURITY DEFINER (convención de la spec 002): el trigger corre con el rol
que escribe, bajo la misma RLS forzada que ya exige ``app.tenant_id`` = tenant
de la fila (el migrador tiene BYPASSRLS). En el borrado de un tenant no queda
nada que versionar: si el tenant ya no existe, el trigger no toca la tabla y la
FK ``ON DELETE CASCADE`` se lleva su fila.
"""
from alembic import op

revision = "0047_policy_revision"
down_revision = "0046_key_rescope_audit"
branch_labels = None
depends_on = None

_TABLES = ("policy_bundles", "model_access")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.policy_revisions (
            tenant_id  uuid PRIMARY KEY
                       REFERENCES public.tenants(id) ON DELETE CASCADE,
            revision   bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE public.policy_revisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.policy_revisions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON public.policy_revisions
          USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON public.policy_revisions TO trust_app")
    op.execute("GRANT ALL ON public.policy_revisions TO trust_migrator")
    op.execute(
        """
        CREATE FUNCTION public.bump_policy_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            t uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                t := OLD.tenant_id;
            ELSE
                t := NEW.tenant_id;
            END IF;
            -- Borrado de un tenant (cascada o purga): nada que versionar.
            IF NOT EXISTS (SELECT 1 FROM public.tenants WHERE id = t) THEN
                RETURN NULL;
            END IF;
            INSERT INTO public.policy_revisions AS r (tenant_id, revision, updated_at)
            VALUES (t, 1, now())
            ON CONFLICT (tenant_id)
            DO UPDATE SET revision = r.revision + 1, updated_at = now();
            RETURN NULL;
        END
        $$
        """
    )
    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_bump_policy_revision "
            f"AFTER INSERT OR UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.bump_policy_revision()"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS {table}_bump_policy_revision ON public.{table}"
        )
    op.execute("DROP FUNCTION IF EXISTS public.bump_policy_revision()")
    op.execute("DROP TABLE IF EXISTS public.policy_revisions")
