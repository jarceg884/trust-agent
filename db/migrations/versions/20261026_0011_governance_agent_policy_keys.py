"""spec 019 (governance sobre Hermes): agent governance policy keys

Incremento 1, D-GOH-08 — cuatro claves tipadas nuevas en el catálogo
``catalog.policy_keys`` (misma maquinaria REQ-GOV-020/023, sin cambios de
semántica: catálogo tipado + no-relajación entre niveles):

- ``allowed_tools`` (list): allowlist de tools del agente Hermes. Default del
  proveedor = tools conversacionales básicas SIN terminal ni browser:
  web_search, read_file, write_file (+ governance_status, tool interna del
  plugin, siempre permitida). La intersección deny-overrides del combinator
  aplica igual que ``allowed_connectors``.
- ``offline_grace_minutes`` (integer, default 60): gracia offline del bundle
  firmado. Piso del proveedor 15 min — enforceado en el upsert (policies.py)
  y en la compilación del bundle (bundle.py::OFFLINE_GRACE_FLOOR_MINUTES).
- ``unbind_mode`` (string enum readonly|lock, default readonly): grado del
  modo restringido al desligar/revocar (ADR-017). El enum vive en
  combinator.STRING_RANKS (lock > readonly en restrictividad).
- ``attestation_overdue_hours`` (integer, default 24): umbral de detección
  de dispositivos sin attestation (D-GOH-06; se consume en el incremento 3).

Los defaults son parte del contrato del bundle §5.2: ``exp = iat +
offline_grace_minutes * 60``.
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_gov_agent_policy_keys"  # FIX lane 017: ≤32 chars (alembic_version.version_num es VARCHAR(32))
down_revision = "0010_roles_access"
branch_labels = None
depends_on = None

_NEW_KEYS = [
    (
        "allowed_tools",
        "list",
        '["web_search", "read_file", "write_file", "governance_status"]',
        "Allowlist of Hermes agent tool ids (deny-overrides across levels); "
        "provider default = basic conversational tools WITHOUT terminal/browser.",
    ),
    (
        "offline_grace_minutes",
        "integer",
        "60",
        "Offline grace for the signed governance bundle (provider floor 15).",
    ),
    (
        "unbind_mode",
        "string",
        '"readonly"',
        "Restricted-mode degree when unbound/revoked: readonly | lock.",
    ),
    (
        "attestation_overdue_hours",
        "integer",
        "24",
        "Hours without attestation before an active device is flagged overdue.",
    ),
]


def upgrade() -> None:
    # FIX (dev lane 017, 2026-10-26): los defaults eran sa.text(...) pasados
    # como VALOR de bindparam — psycopg no puede adaptar un TextClause y
    # `alembic upgrade head` reventaba para toda la factory. Ahora son strings
    # JSON planos casteados a jsonb en el INSERT (mismo resultado en DB).
    for key, value_type, default_json, description in _NEW_KEYS:
        op.execute(
            sa.text(
                "INSERT INTO catalog.policy_keys "
                "(key, value_type, invariable, default_value, description) "
                "VALUES (:k, :vt, false, CAST(:dv AS jsonb), :d) "
                "ON CONFLICT (key) DO NOTHING"
            ).bindparams(k=key, vt=value_type, dv=default_json, d=description)
        )


def downgrade() -> None:
    for key, _, _, _ in _NEW_KEYS:
        op.execute(
            sa.text("DELETE FROM catalog.policy_keys WHERE key = :k").bindparams(k=key)
        )
