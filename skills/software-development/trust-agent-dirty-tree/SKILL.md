---
name: trust-agent-dirty-tree
description: El repo /root/trust-agent es un árbol WIP compartido sin commits por tarea — jamás uses `git stash` para aislar cambios; verifica pre-existencia moviendo solo TUS archivos.
---

# El árbol de trust-agent es WIP compartido — nunca `git stash`

Verificado 2026-10-27 (t_e03bba6c): el working tree de `/root/trust-agent` lleva
**170+ archivos modificados y ~170 untracked sin commit** (trabajo acumulado de
varios perfiles sobre 3 commits viejos). No hay integración por rama: el árbol
ES el estado compartido del equipo.

## Regla

**`git stash -u` traga el trabajo de TODOS los perfiles** (211 archivos), y el
`pop` puede fallar parcialmente (colisión de untracked recreados, p. ej.
`__pycache__` de una corrida de tests) dejando las modificaciones tracked
DENTRO del stash y el árbol en estado HEAD — silenciosamente.

## Cómo verificar pre-existencia de un fallo sin stash

- Mueve/re nombra SOLO los archivos que TU tarea añadió (p. ej. el módulo nuevo
  + reverte el import en el archivo tocado) y corre el test; luego restaura.
- O corre el test sospechoso en aislado y la corrida completa con
  `--ignore=<tu-test>` y compara el set de fallos: si es idéntico, el fallo es
  pre-existente (patrón usado con éxito en t_e03bba6c para atribuir los 4
  fallos order-dependent de `test_provision.py`).

## Si ya ocurrió el stash parcial

1. `git checkout stash@{0} -- .` restaura TODOS los tracked del árbol del stash.
2. `git reset` (mixed) para des-estagear.
3. Los archivos DELETED en el stash: `git diff --diff-filter=D -z --name-only
   stash@{0}^ stash@{0} | xargs -0 -r rm -f`.
4. Verifica: `git status --short | awk '{print $1}' | sort | uniq -c` debe
   devolver los mismos conteos M/D/?? que antes del stash; recién entonces
   `git stash drop`.

## Bind mounts de dirs no-versionados mueren en silencio

Verificado 2026-10-27 (t_689fda26): `git stash -u`/`git clean -fd` borra un dir
untracked que es fuente de un bind mount de Docker (p. ej. el tema white-label
`platform/idp-themes/` montado en `idp`). Al recrearse el dir cambia el inode y
el mount del contenedor queda stale/vacío SIN error — Keycloak sirvió el tema
stock + favicon caballo durante horas (regresión silenciosa de AC-AUTH-001).

- Todo asset compartido que alimente un bind mount EN VIVO debe estar
  **trackeado en git** (idp-themes lo está desde a6563f7).
- El healthcheck de `idp` incluye `test -f .../theme.properties`: un mount
  stale se ve como contenedor `unhealthy` en `docker ps` → `docker compose
  restart idp` lo cura.

## La DB efímera de la suite es COMPARTIDA — aísla con TRUST_TEST_DB

Verificado 2026-10-27 (t_8f716f9f): el fixture session-scoped `db` del
conftest hace `DROP DATABASE IF EXISTS trust_agent_platform_test` al arrancar.
Si OTRA lane corre la suite a la vez, el DROP revienta con
`psycopg.errors.ObjectInUse` y la corrida entera se llena de cientos de
ERROR en fixtures (falso rojo masivo).

- Corre CADA corrida propia con `TRUST_TEST_DB=<nombre-único-por-lane>`
  (el conftest lo honra; alembic + seed corren igual).
- Keycloak (127.0.0.1:8080) NO se puede aislar: los slugs de realm de
  `test_provision.py`/e2e son fijos — antes de correr provision/e2e,
  verifica con `ps aux | grep pytest` que ninguna otra suite esté activa.
- El árbol cambia DURANTE tu corrida: atribuye fallos con `find
  platform/tests -newermt <hora-de-tu-corrida>` + `git status` antes de
  asumir que tu patch los causó (t_8f716f9f: 17 fallos "nuevos" eran WIP
  de lanes concurrentes; test_provision pasó 10/10).
