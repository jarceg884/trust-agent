"""Migración 0018: branding_versioning (spec 028, REQ-BRD-004 de la spec 001).

Flujo draft/publish del branding white-label:

- ``tenants`` += ``branding_draft`` (jsonb, estado de edición — NUNCA visible
  para usuarios, REQ-BRD-004) y ``branding_version`` (int, versión publicada).
- ``branding_assets``: logos light/dark + favicon por tenant (PNG/SVG ≤ 2 MB)
  en ``bytea`` — decisión D-028-01: NO en el jsonb de tenants (aislamiento
  RLS uniforme, backup pg_dump, publish atómico en la misma tx). Cada upload
  inserta una fila nueva; los assets referenciados por versiones publicadas
  jamás se mutan.
- ``branding_versions``: historial append-only de lo publicado
  (data jsonb exacto + published_by/published_at). Publish = INSERT versión
  N+1 + UPDATE tenants.branding en UNA transacción; rollback = re-publicar
  una versión anterior como versión nueva (D-028-03).
- Nuevas acciones de audit (extensión data-only del enum versionado,
  spec 002 §3.5): ``branding.draft.updated``, ``branding.published``,
  ``branding.rolled_back``, ``branding.asset.uploaded``.

Paridad canónica: db/schema.sql §16, requerida por db/tests/test_schema_parity.py.

Convención del proyecto: DDL en SQL crudo vía op.execute (las tablas
referenciadas viven en migraciones anteriores, fuera del metadata de esta
revisión — op.create_table no puede resolver el FK).

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_branding_versioning"
down_revision = "0017_live_policy_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Draft de edición + versión publicada (D-028-02).
    op.execute(sa.text("""
        ALTER TABLE tenants
          ADD COLUMN IF NOT EXISTS branding_draft jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN IF NOT EXISTS branding_version integer NOT NULL DEFAULT 0
    """))

    # Assets de branding por tenant (D-028-01): bytea, append-only.
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS branding_assets (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id   uuid NOT NULL REFERENCES tenants(id),
          kind        text NOT NULL
                        CHECK (kind IN ('logo_light','logo_dark','favicon')),
          mime        text NOT NULL CHECK (mime IN ('image/png','image/svg+xml')),
          size_bytes  integer NOT NULL
                        CHECK (size_bytes > 0 AND size_bytes <= 2097152),
          sha256      text NOT NULL,
          content     bytea NOT NULL,
          created_by  uuid,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS branding_assets_tenant_kind_idx "
        "ON branding_assets (tenant_id, kind)"))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS branding_assets_tenant_id_idx "
        "ON branding_assets (tenant_id)"))

    # Historial de versiones publicadas (D-028-03): append-only.
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS branding_versions (
          tenant_id    uuid NOT NULL REFERENCES tenants(id),
          version      integer NOT NULL CHECK (version >= 1),
          data         jsonb NOT NULL,
          published_by uuid,
          published_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, version)
        )
    """))

    # Comentarios de catálogo (paridad pg_dump con db/schema.sql §16).
    op.execute(sa.text(
        "COMMENT ON TABLE branding_assets IS 'Tenant branding assets "
        "(spec 028, REQ-BRD-001/004): logo light/dark + favicon, PNG/SVG "
        "up to 2 MB, stored as bytea (D-028-01 — never inside "
        "tenants.branding jsonb). Append-only: every upload inserts a new "
        "row; assets referenced by published versions are never mutated.'"
    ))
    op.execute(sa.text(
        "COMMENT ON TABLE branding_versions IS 'Published branding history "
        "(spec 028, D-028-03): exact jsonb published per version. Publish "
        "is atomic (INSERT next version + UPDATE tenants.branding in one "
        "tx); rollback republishes an older version as a NEW version — "
        "the history is append-only.'"
    ))

    # RLS: mismo patrón fail-closed + FORCE que el resto del negocio.
    for table in ("branding_assets", "branding_versions"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON public.{table}
              FOR ALL TO trust_app
              USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
              WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """)
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} "
            "TO trust_app"
        )

    # Enum versionado de audit: extensión por datos (nunca bypass del FK).
    op.execute(sa.text("""
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('branding.draft.updated',
           'Tenant branding draft edited (not yet visible to users — REQ-BRD-004).'),
          ('branding.published',
           'Tenant branding draft published atomically as a new version.'),
          ('branding.rolled_back',
           'Tenant branding rolled back to a previous published version.'),
          ('branding.asset.uploaded',
           'Branding asset uploaded (logo light/dark or favicon, PNG/SVG <= 2 MB).')
        ON CONFLICT (action) DO NOTHING;
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM catalog.audit_actions WHERE action IN (
          'branding.draft.updated', 'branding.published',
          'branding.rolled_back', 'branding.asset.uploaded')
    """))
    for table in ("branding_versions", "branding_assets"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
        op.execute(f"DROP TABLE IF EXISTS public.{table}")
    op.execute(sa.text(
        "ALTER TABLE tenants DROP COLUMN IF EXISTS branding_version"))
    op.execute(sa.text(
        "ALTER TABLE tenants DROP COLUMN IF EXISTS branding_draft"))
