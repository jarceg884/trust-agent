# Trust Agent — control plane (API + workers + migrator de Alembic).
# Contexto de build: raíz del repo (/root/trust-agent) porque alembic.ini
# apunta a ../db/migrations (única línea de migraciones, D-CP-11).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/trust-agent/platform

# Dependencias + paquete (app/) + herramientas de operación.
COPY platform/pyproject.toml platform/alembic.ini ./
COPY platform/app ./app
COPY platform/scripts ./scripts
COPY platform/tests/litellm_fake.py ./tests/litellm_fake.py
# Vía canónica de migraciones (spec 002): db/migrations.
COPY db/migrations /opt/trust-agent/db/migrations

RUN pip install --no-cache-dir .

# No root: la app SIEMPRE conecta como rol trust_app (sin BYPASSRLS);
# el proceso tampoco necesita root dentro del contenedor.
RUN useradd --system --no-create-home trust && chown -R trust:trust /opt/trust-agent
USER trust

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
