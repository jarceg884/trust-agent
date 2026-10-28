"""Tenant deprovisioning (t_4f4b0eb1, repro Miller): catálogo para DELETE.

Extensión por datos (I10, arq. §5.6) de tres piezas:

  1. ``tenant.deleted`` — acción de audit nueva. El evento vive en
     ``ops.provider_audit`` (plano proveedor): ``audit_log`` exige
     ``tenant_id`` + FK a ``tenants`` y se borra CON el tenant, así que el
     registro durable de un deprovisioning no puede vivir ahí (misma
     frontera documentada de spec 025 D-PS-07).
  2. ``tenants.delete`` — permiso nuevo, MÁS restrictivo que
     ``tenants.provision``: borrar un tenant es irreversible (realm +
     todas las filas). El check del router es data-driven (D-ONB-02):
     quitar la fila ``platform_admin -> tenants.delete`` de
     ``catalog.role_permissions`` debe negar el endpoint.
  3. Grant ``platform_admin -> tenants.delete`` para que el super-user
     del proveedor conserve la superficie completa.

Paridad schema.sql: los mismos INSERT viven en db/schema.sql (sección 3.x),
exigida por db/tests/test_schema_parity.py.
"""
from alembic import op

revision = "0031_tenant_deprovision"
down_revision = "0030_auth_panel_dances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('tenant.deleted',
           'Tenant deprovisioned by provider: Keycloak realm + every row removed (irreversible). Event lives in ops.provider_audit.')
        ON CONFLICT (action) DO NOTHING;

        INSERT INTO catalog.permissions (key, description) VALUES
          ('tenants.delete',
           'Deprovision/delete tenants and all their data (provider, irreversible).')
        ON CONFLICT (key) DO NOTHING;

        INSERT INTO catalog.role_permissions (role_key, permission_key) VALUES
          ('platform_admin', 'tenants.delete')
        ON CONFLICT (role_key, permission_key) DO NOTHING;
        """
    )
    # Grant healing (verificado en la DB real): las tablas creadas DESPUÉS
    # del grant blanket de 0001 (agents, agent_grants, notifications,
    # vault_connections, branding_assets, branding_versions) nunca
    # recibieron DELETE para trust_migrator — el deprovisioning corre como
    # migrator y revienta con InsufficientPrivilege. Re-grant blanket
    # idempotente, las MISMAS sentencias que schema.sql §8/22 (paridad
    # pg_dump de db/tests/test_schema_parity.py).
    op.execute(
        """
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public, catalog, ops TO trust_migrator;
        GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO trust_migrator;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM catalog.role_permissions
         WHERE role_key = 'platform_admin' AND permission_key = 'tenants.delete';
        DELETE FROM catalog.permissions WHERE key = 'tenants.delete';
        DELETE FROM catalog.audit_actions WHERE action = 'tenant.deleted';
        """
    )
