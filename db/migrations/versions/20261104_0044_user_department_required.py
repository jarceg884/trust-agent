"""Migración 0044: todo usuario pertenece obligatoriamente a un departamento.

Regla de producto (mandato Miller): no existen usuarios "org-level" sin
departamento — las políticas, budgets y presets se resuelven por departamento.
Backfill: para cada tenant sin departamentos se crea "General"; los usuarios
con department_id NULL se asignan al departamento "General" (o al primero
creado). Luego la columna pasa a NOT NULL.
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_user_department_required"
down_revision = "0043_model_cache_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Cada tenant debe tener al menos un departamento.
    op.execute(sa.text(
        "INSERT INTO public.departments (id, tenant_id, name) "
        "SELECT gen_random_uuid(), t.id, 'General' FROM public.tenants t "
        "WHERE NOT EXISTS (SELECT 1 FROM public.departments d "
        "                  WHERE d.tenant_id = t.id)"
    ))
    # 2. Backfill: 'General' si existe; si no, el primero por created_at.
    op.execute(sa.text(
        "UPDATE public.users u SET department_id = d.id "
        "FROM (SELECT DISTINCT ON (tenant_id) tenant_id, id "
        "      FROM public.departments "
        "      ORDER BY tenant_id, (name = 'General') DESC, created_at) d "
        "WHERE u.tenant_id = d.tenant_id AND u.department_id IS NULL"
    ))
    # 3. Invariante en la base: no hay usuario sin departamento.
    op.execute(sa.text(
        "ALTER TABLE public.users ALTER COLUMN department_id SET NOT NULL"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE public.users ALTER COLUMN department_id DROP NOT NULL"
    ))
