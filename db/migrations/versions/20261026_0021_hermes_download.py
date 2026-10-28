"""Migración 0021: hermes_download (spec 023, t_526030f3).

Download Hermes por tenant — seam link→app (revisión maestra t_ef306f32):

- ``tenants`` += 2 columnas (D-023-08): estado del link público de descarga
  por tenant, gestionado (enable/disable) por el ADMIN DEL CLIENTE desde el
  Admin Console (corrección Miller 2026-10-26; contrato GET/PUT
  /api/download-link, sección "Hermes Download" de t_360a752e):
  ``download_enabled`` — link habilitado por defecto (el admin lo deshabilita
  a propósito; un tenant recién creado es descargable sin tocar nada);
  ``download_updated_at`` — sello del último toggle (contrato ``updated_at``).
- Permiso nuevo ``download_link.manage`` (D-023-09): SOLO ``org_admin`` en la
  matriz RBAC — coherente con el gate de rol de la UI (dept_admin NO gestiona
  el link de la organización). Extensión data-only del catálogo versionado.
- Acciones de audit ``download.issued`` (por descarga del bundle, REQ-HDL-004),
  ``download.link.enabled`` / ``download.link.disabled`` (toggles auditados,
  REQ-HDL-005) — nunca bypass del FK de ``audit_log.action``.

Paridad canónica: db/schema.sql §5.1 (tenants) + §2 (catalog.permissions /
role_permissions) + §3 (catalog.audit_actions), requerida por
db/tests/test_schema_parity.py.

Creada vía db/scripts/migration_guard.py (lock de serialización — ADR 0001).
"""
from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "0021_hermes_download"
down_revision = "0020_device_attestation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
        "download_enabled boolean NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS "
        "download_updated_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "INSERT INTO catalog.permissions (key, description) VALUES "
        "  ('download_link.manage', "
        "   'Enable/disable the public Hermes download link.') "
        "ON CONFLICT (key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO catalog.role_permissions (role_key, permission_key) "
        "VALUES ('org_admin', 'download_link.manage') "
        "ON CONFLICT (role_key, permission_key) DO NOTHING"
    )
    op.execute(
        "INSERT INTO catalog.audit_actions (action, description) VALUES "
        "  ('download.issued', "
        "   'Hermes bundle downloaded from the public link.'), "
        "  ('download.link.enabled', "
        "   'Public download link enabled by the organization admin.'), "
        "  ('download.link.disabled', "
        "   'Public download link disabled by the organization admin.') "
        "ON CONFLICT (action) DO NOTHING"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "  ('download.issued', 'download.link.enabled', 'download.link.disabled')"
    )
    op.execute(
        "DELETE FROM catalog.role_permissions "
        "WHERE role_key = 'org_admin' AND permission_key = 'download_link.manage'"
    )
    op.execute(
        "DELETE FROM catalog.permissions WHERE key = 'download_link.manage'"
    )
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS download_updated_at")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS download_enabled")
