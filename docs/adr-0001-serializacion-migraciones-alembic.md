# ADR 0001 — Serialización de migraciones Alembic entre workers simultáneos

- **Estado**: aceptado
- **Fecha**: 2026-10-26
- **Contexto**: tarea kanban `t_2be18285` (hallazgo de la revisión maestra `t_ef306f32`, ciclo 1)
- **Decisiones relacionadas**: D-CP-11 (única línea de migraciones), lección `t_42131101` (migrate exit 255)

## Contexto

Varias lanes kanban dev trabajan en paralelo sobre el mismo árbol de
`/root/trust-agent` y varias de ellas necesitan crear migraciones Alembic.
El 2026-10-26 la carpeta `db/migrations/versions/` acumuló **tres ramas 0009
paralelas** (`0009_default_agent_profiles`, `0009_departments_crud`,
`0009_model_catalog_maas`), todas con `down_revision = 0008_...`: tres workers
leyeron el head, y cada uno apiló "sobre el head" que habían leído antes de que
los demás escribieran. La cadena quedó válida solo gracias a la merge
`12c08d4fb858`. Con 3+ tareas dev activas que crearán migraciones, el incidente
**iba a repetirse**.

La convención documentada ("anuncia en tu card, usa el head actual") es
necesaria pero insuficiente: no elimina la carrera de lectura-escritura del
directorio.

## Decisión

Dos barreras complementarias, ambas en `db/scripts/migration_guard.py`
(solo stdlib, corre en el host y dentro de la imagen):

1. **Barrera preventiva — lock de archivo para CREAR migraciones.**
   `python db/scripts/migration_guard.py new --slug <slug>` es la única vía
   bendecida para crear una migración:
   - toma un `flock` exclusivo sobre `db/migrations/.versions.lock`
     (espera activa con timeout de 180 s, configurable);
   - **dentro del lock** lee el head real y el siguiente `NNNN`, y escribe
     `YYYYMMDD_NNNN_<slug>.py` ya apilado (`down_revision` = head del
     instante, `revision` ≤ 32 chars);
   - si la cadena ya está bifurcada, se niega a crear (fail-closed).

   El lock serializa la ventana leer-head → escribir-archivo que es
   exactamente donde ocurría la carrera.

2. **Barrera detectiva — check de grafo que rechaza bifurcaciones.**
   `python db/scripts/migration_guard.py check` valida el grafo sin tocar la
   DB: única cabeza, ids no duplicados, padres existentes, sin ciclos,
   ids ≤ 32 chars. Está cableado al servicio `migrate` de compose
   (corre ANTES de `alembic upgrade head`), de modo que un árbol bifurcado
   muere con un mensaje accionable ("MULTIPLES CABEZAS… re-apila o emite una
   merge") en lugar del críptico `exit 255: Can't locate revision`.

   Un fork **mergeado** (como el histórico 0009) tiene una sola cabeza y
   sigue siendo válido: el check no lo rechaza.

## Alternativas descartadas

- **Numeración por orden de claim del kanban**: no cierra la carrera (el
  claim no ordena la escritura de archivos) y acopla el repo al scheduler.
- **Solo check en CI**: no hay CI real en este entorno dev-local; y un check
  post-hoc no evita el trabajo manual de re-apilar.
- **Advisory lock de PostgreSQL**: protege la DB, no el árbol de archivos,
  que es donde ocurre la bifurcación.

## Consecuencias

- Crear una migración a mano (sin el guardián) queda prohibido por
  convención; el check del servicio `migrate` es la red de seguridad.
- El archivo `.versions.lock` es un artefacto de runtime (ignorado por git).
- Suite de tests del guardián: `db/tests/test_migration_guard.py` (13 casos,
  sin DB).
- Evidencia real de la demostración (dos workers en paralelo, con y sin
  mecanismo): `docs/evidencia/serializacion-migraciones-20261026.md`.
