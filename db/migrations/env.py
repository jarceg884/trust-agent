"""Alembic environment — Trust Agent data layer.

Migraciones de SQL puro (la fuente canónica del DDL es db/schema.sql; la
migración inicial lo ejecuta embebido y un test de paridad exige igualdad).
La URL de conexión viene de la variable de entorno DATABASE_URL.
"""
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:trustlocal@127.0.0.1:55432/trust_agent",
)
config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
