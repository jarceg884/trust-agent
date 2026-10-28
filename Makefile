# Trust Agent — tareas del operador (t_b62edff2, "desplegable donde sea").
# Requisitos: Docker + plugin compose v2, OpenSSL (genera secretos setup.sh).

.PHONY: setup up down ps logs smoke backup restore litellm-up clean

## setup   — instalación de un comando: .env + secretos + build + up + smoke
setup:
	scripts/setup.sh

## up      — levantar el stack (ya configurado)
up:
	docker compose up -d

## down    — apagar (los datos persisten en volúmenes)
down:
	docker compose down

## ps      — estado de los servicios
ps:
	docker compose ps

## logs    — logs del control plane
logs:
	docker compose logs -f api

## smoke   — verificación de que levantó bien (healthz + login + IdP)
smoke:
	scripts/smoke.sh

## backup  — respaldo pg_dump de la DB → trust-agent-backup-<ts>.dump
backup:
	scripts/backup.sh

## restore — restaurar un dump: make restore DUMP=<archivo.dump>
restore:
	scripts/restore.sh $(DUMP)

## litellm-up — proxy LiteLLM REAL de producción (perfil litellm) + su DB
litellm-up:
	docker compose --profile litellm up -d

## clean   — apagar Y BORRAR datos (DB + Keycloak). Irreversible.
clean:
	docker compose down -v
