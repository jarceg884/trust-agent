"""Spec 015 (Auth UX, feedback Miller #10): MFA pasa de piso invariable del
proveedor a policy variable por organización, y se habilita el
auto-registro.

- ``require_mfa`` → ``invariable=false``, ``default_value=false``: los
  tenants NUEVOS nacen con MFA opcional (banner que sugiere enrolarlo);
  los tenants EXISTENTES conservan su fila explícita en ``policies``
  (``require_mfa=true`` insertada en provisioning cuando era invariable)
  — el org_admin puede apagarla vía la API de policies, nunca la
  migración.
- Nueva clave ``allow_self_registration`` (boolean, variable, default
  ``true``): gatilla el auto-registro con contraseña (D-AUTH-04) y la
  auto-provisión en el primer login brokered (D-AUTH-05).
- Nuevas acciones de audit ``user.self_registered`` y
  ``user.auto_provisioned`` (extensión data-only del enum versionado,
  spec 002 §3.5 — nunca bypass del FK de ``audit_log.action``).

Paridad canónica: db/schema.sql §3 (catalog.policy_keys +
catalog.audit_actions), requerida por db/tests/test_schema_parity.py.
"""
from alembic import op
import sqlalchemy as sa


revision = "0014_auth_ux_policies"
down_revision = "0013_device_rejected_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE catalog.policy_keys
           SET invariable = false,
               default_value = 'false'::jsonb,
               description = 'MFA mandatory for every authenticated operation (org-tunable; when false the app always suggests enrolling).'
         WHERE key = 'require_mfa';

        INSERT INTO catalog.policy_keys (key, value_type, invariable, default_value, description) VALUES
          ('allow_self_registration', 'boolean', false, 'true',
           'Users can create their own account (password self-registration or first Google login).')
        ON CONFLICT (key) DO NOTHING;

        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('user.self_registered', 'User created their own account with a password (spec 015 D-AUTH-04).'),
          ('user.auto_provisioned', 'User auto-provisioned on first brokered login (spec 015 D-AUTH-05).')
        ON CONFLICT (action) DO NOTHING;
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM catalog.audit_actions WHERE action IN "
        "('user.self_registered', 'user.auto_provisioned')"
    ))
    op.execute(sa.text(
        "DELETE FROM catalog.policy_keys WHERE key = 'allow_self_registration'"
    ))
    op.execute(sa.text("""
        UPDATE catalog.policy_keys
           SET invariable = true,
               default_value = 'true'::jsonb,
               description = 'MFA mandatory for every authenticated operation.'
         WHERE key = 'require_mfa';
    """))
