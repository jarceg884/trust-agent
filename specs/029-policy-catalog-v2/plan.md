# Plan — Spec 029

1. Migración `0022_policy_catalog_v2` (migration_guard, ADR 0001): INSERT de las 29
   keys en `catalog.policy_keys` + downgrade DELETE.
2. Paridad `db/schema.sql` §3.2: mismas 29 filas en el INSERT canónico.
3. `platform/app/governance/combinator.py`: `STRING_RANKS["approval_mode"]`,
   `TRUE_WINS_KEYS` (OR para `require_write_sandbox`/`require_checkpoints`),
   `DENY_UNION_KEYS` (unión para `denied_command_patterns`) y combinador de map
   específico para `web_domain_policy`.
4. `platform/app/governance/policies.py`: `INTEGER_FLOORS` para
   `max_delegation_depth` (0) y `session_retention_days` (1); validación estructural
   de `web_domain_policy`.
5. Tests `platform/tests/test_policy_catalog_v2.py`: AC-029-1..4 contra la API real
   (+ tests unitarios del combinator para los casos puros).
6. Verificación: `migration_guard.py check`, `db` parity test, suite platform,
   `docker compose build migrate && docker compose up -d`.
7. Coordinación: comentario en t_150e8931 / t_f99e12ab (keys nuevas disponibles en
   el catálogo; claims aditivos REQ-029-010).
