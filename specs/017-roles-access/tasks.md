# Tasks — Spec 017 (roles-access)

- [x] T1 — spec.md / plan.md / tasks.md (fase specify+plan).
- [x] T2 — Migración `0010_roles_access` + `db/schema.sql` §13 (permiso
  `roles.assign` solo para org_admin; paridad pg_dump).
- [x] T3 — `app/org/roles.py`: assign_role / revoke_role con guards
  D-17-02/03/04 y audit.
- [x] T4 — `app/org/router.py`: POST/DELETE roles, GET /departments,
  `status` en `UserUpdateBody` (hotspot: append, re-leer antes).
- [x] T5 — `app/org/directory.py`: `status` en update_user +
  `_disable_core` compartido; `departments.list_departments`.
- [x] T6 — Tests `test_roles_access.py` (AC-RA-001..006) + suite completa en
  verde.
- [x] T7 — UI `users-page.tsx` completa + `types.ts` (append) + build/lint
  console en verde.
- [x] T8 — Rebuild imagen platform + `smoke_roles_access.py` contra stack
  vivo (AC-RA-008) + doc de evidencia.
- [x] T9 — Converge: implementación == spec, ACs con evidencia real,
  completar card.

> Nota de convergencia (2026-10-26, run 137): verificación re-ejecutada de
> primera mano tras los rebuilds de lanes hermanas — `test_roles_access.py`
> 15/15 PASS; `smoke_roles_access.py` 28/28 PASS (servidor real efímero +
> Alembic canónico); `smoke_roles_access_shared.py` 16/16 PASS contra el
> stack compartido vivo (:8000 + Keycloak real + login PKCE S256 + TOTP
> acr=2); rutas POST/DELETE roles + PUT users + disable visibles en el
> openapi.json del API vivo; imagen trust-agent-console (:3100, rebuild
> 17:26 UTC) sirve el bundle con la UI de roles (`roles.assign` verificado
> en /app/.next). Evidencia: docs/evidencia/017-roles-access-evidencia.md.
