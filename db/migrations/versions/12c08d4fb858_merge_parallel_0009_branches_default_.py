"""merge parallel 0009 branches (default_agent_profiles + departments_crud)

Revision ID: 12c08d4fb858
Revises: 0009_default_agent_profiles, 0009_departments_crud, 0009_model_catalog_maas
Create Date: 2026-10-26 08:36:16.487240
"""
from alembic import op
import sqlalchemy as sa


revision = '12c08d4fb858'
down_revision = ('0009_default_agent_profiles', '0009_departments_crud', '0009_model_catalog_maas')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
