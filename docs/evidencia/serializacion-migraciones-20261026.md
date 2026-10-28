# Evidencia: serialización de migraciones Alembic (tarea t_2be18285, 2026-10-26)

Ejecución real en `/root/trust-agent` (nada simulado). Mecanismo: ADR 0001
(`docs/adr-0001-serializacion-migraciones-alembic.md`), implementado en
`db/scripts/migration_guard.py`.

## 0. Cadena actual verificada (entregable 1)

```
$ python3 db/scripts/migration_guard.py check
versions dir : /root/trust-agent/db/migrations/versions
revisiones   : 20
head         : 0017_live_policy_presence (20261026_0017_live_policy_presence.py)
WARN: 12c08d4fb858_merge_parallel_0009_branches_default_.py: fuera de la convención YYYYMMDD_NNNN_slug.py
WARN: NNNN 0009 usado por 3 archivos (legado del incidente 0009): ...
OK: cadena Alembic válida — única cabeza, sin duplicados ni ciclos.   exit=0

$ cd db && .venv/bin/alembic heads
0017_live_policy_presence (head)

$ .venv/bin/alembic upgrade head        # contra DB de dev (trust-agent-db :55432)
UPGRADE_CLEAN=0                          # no-op: ya estaba en head

$ .venv/bin/alembic current
0017_live_policy_presence (head)
```

## 1. Repro del incidente SIN mecanismo (dos workers escriben a mano en paralelo)

Dos archivos escritos simultáneamente, ambos con `down_revision =
"0017_live_policy_presence"` (lo que cada worker leyó como "head"):

```
$ .venv/bin/alembic heads
test_lane_a (head)
test_lane_b (head)          # ← la bifurcación: 2 cabezas

$ python3 db/scripts/migration_guard.py check
ERROR: MULTIPLES CABEZAS (2) — bifurcación de la cadena:
  - head 'test_lane_a' (20261026_0018_test_lane_a.py)
  - head 'test_lane_b' (20261026_0018_test_lane_b.py)
Corrige re-apilando: la lane que anunció DESPUÉS debe cambiar su
down_revision al head de la lane anterior (o emite una revisión merge). ...
exit=1
```

Extra (fail-closed): con esos archivos fork aún presentes, dos `new`
paralelos se NEGARON a crear migraciones ("FAIL: la cadena actual ya está
bifurcada/rota; corrígela antes de crear migraciones nuevas"), exit 1 ambos.

## 2. CON mecanismo: dos `migration_guard.py new` lanzados en paralelo real

Driver: `subprocess.Popen` de dos procesos simultáneos (pids 209843/209844),
salida completa de cada uno:

```
[driver] lanzado worker a (pid 209843)
[driver] lanzado worker b (pid 209844)

===== salida worker a (exit 0) =====
lock ocupado por otro worker — esperando (timeout 180s)...
creada      : db/migrations/versions/20261026_0019_test_worker_a.py
revision    : 0019_test_worker_a
apilada sobre head anterior: 0018_test_worker_b
...
OK: cadena Alembic válida — única cabeza, sin duplicados ni ciclos.

===== salida worker b (exit 0) =====
creada      : db/migrations/versions/20261026_0018_test_worker_b.py
revision    : 0018_test_worker_b
apilada sobre head anterior: 0017_live_policy_presence
...
OK: cadena Alembic válida — única cabeza, sin duplicados ni ciclos.

$ .venv/bin/alembic heads
0019_test_worker_a (head)    # ← UNA sola cabeza: 0017 → 0018 → 0019 lineal
```

El worker B tomó el lock primero y creó 0018 sobre 0017; el worker A
**esperó** el lock y creó 0019 apilado sobre 0018. La bifurcación es
imposible por construcción: el head se lee DENTRO del lock.

## 3. Limpieza

Los archivos de prueba (fork y apilados) fueron eliminados; el árbol quedó
en head `0017_live_policy_presence` con `check` OK.

## 4. Red de seguridad en el servicio `migrate` (imagen)

`docker-compose.yml` (servicio `migrate`) ahora corre el check ANTES del
upgrade. Build y corrida reales:

```
$ docker compose build migrate && docker compose up -d --no-deps migrate
... migrate  Built / Container trust-agent-migrate  Recreated / Started

$ docker inspect trust-agent-migrate --format 'status={{.State.Status}} exit={{.State.ExitCode}}'
status=exited exit=0

$ docker logs trust-agent-migrate
versions dir : /opt/trust-agent/db/migrations/versions
revisiones   : 20
head         : 0017_live_policy_presence (20261026_0017_live_policy_presence.py)
WARN: ... (legado 0009)
OK: cadena Alembic válida — única cabeza, sin duplicados ni ciclos.
```

## 5. Tests

`db/tests/test_migration_guard.py`: 13 casos (sin DB) — cadena lineal OK,
rechazo de 2 cabezas, aceptación de fork mergeado (caso 0009), padre
inexistente, id duplicado, id > 32 chars, archivo roto, directorio vacío,
apilado sobre head, apilado secuencial en cadena, rechazo con cadena rota,
slug inválido, truncado a 32.

```
$ .venv/bin/python -m pytest tests/test_migration_guard.py -q
13 passed in 0.08s
```
