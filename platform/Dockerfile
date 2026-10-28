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
# Plugin trust-agent-governance empaquetado en el bundle de descarga (spec
# 023): el API lo copia al tarball desde este árbol (settings.hermes_plugin_dir).
COPY platform/hermes/plugins /opt/trust-agent/hermes-plugins
# Guardián de serialización (ADR 0001): el servicio migrate lo corre ANTES del
# upgrade para rechazar árboles bifurcados con mensaje accionable.
COPY db/scripts/migration_guard.py /opt/trust-agent/db/scripts/migration_guard.py

RUN pip install --no-cache-dir .

# No root: la app SIEMPRE conecta como rol trust_app (sin BYPASSRLS);
# el proceso tampoco necesita root dentro del contenedor.
RUN useradd --system --no-create-home trust && chown -R trust:trust /opt/trust-agent
USER trust

EXPOSE 8000
# Host/puerto desde el entorno (t_b62edff2, portabilidad): default dev
# 127.0.0.1:8000; en un servidor con IP propia fija TRUST_API_HOST=0.0.0.0
# (y el puerto con TRUST_API_PORT) en el .env — sin rebuild de imagen.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host ${TRUST_API_HOST:-127.0.0.1} --port ${TRUST_API_PORT:-8000}"]
