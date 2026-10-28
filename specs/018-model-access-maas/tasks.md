# Tasks — spec 018

- [x] T1 Skill `skills/llm-gateway/huawei-maas-models` (catálogo, precios con fuente, límites, verificación en vivo)
- [x] T2 Migración `0009_model_catalog_maas` + espejo en `db/schema.sql` (columnas contexto, precios numeric, 6 modelos reales, retiro de los 2 genéricos)
- [x] T3 Backend: `GET /api/models` y `access_overview` con contexto + sección `policy`; `list_level` con `model_approvals`
- [x] T4 Frontend: columna Context + bloque Governance policy en Model Access (`types.ts`, `models-page.tsx`)
- [x] T5 `litellm-real/config.yaml` con los 6 deployment names y pricing real
- [x] T6 Tests actualizados (platform + db) y suites en verde
- [x] T7 Evidencia en vivo: MaaS `/models` ↔ catálogo, proxy LiteLLM real `/v1/models` + invocación glm-5.2, `/api/models/access` + `/api/policies` con las secciones nuevas; migración aplicada en dev
- [x] T8 (D-018-06, GAP M-1) `approve()`/`revoke()` resuelven `department_id` a nivel user desde `users` (tenant-scoped) + validación de scope por nivel (422 en vez de CheckViolation 500: user sin user_id, dept sin department_id, org con scope, mismatch de depto, usuario sin depto, cross-tenant 404)
- [x] T9 (D-018-06) Tests de regresión contra la DB real (constraint vivo del conftest Alembic): repro del PUT level=user sin department_id (el caso exacto de la UI) → 201 + fila con depto resuelto; simétrico revoke; 422s del contrato; cross-tenant 404
- [x] T10 (D-018-06) Evidencia en vivo post-fix: repro del GAP M-1 contra el stack vivo → 201, fila en DB con depto resuelto, revoke → 200, intersección deny-all/restore, verificación desde la UI del console (Models → Approve → Level=User)

## Notas de convergencia M-1 (2026-10-27, t_f75e6a18)

- RED verificado sin tocar el árbol compartido: plugin `m1_redcheck.py`
  neutraliza `_resolve_scope` y el test nuevo revienta con el
  `CheckViolation` exacto — la regresión reproduce el constraint vivo.
- `test_model_access.py` 12/12; suites relacionadas (governance, policies,
  bundle, catalogs, connections, discovery, attestation, batch1,
  schema-extensible) 129 passed.
- Evidencia completa en `docs/evidencia/model-access-maas/m1-user-level-fix.md`
  (repro pre-fix 500 + traceback, post-fix 201/200/422, UI con navegador
  real: toast "approved at user level", badges `org user`, fila en DB con
  depto resuelto, audit con depto resuelto).

## Notas de convergencia (2026-10-26)

- T6 incluyó reparar el e2e (`test_07_spend_cutoff_and_recovery`): los montos
  hardcodeados asumían $1/llamada del catálogo viejo; con pricing real MaaS
  (36¢/llamada en el doble) ahora se calculan con `litellm_fake.per_call_cents`.
- T2 requirió dos ajustes de paridad AC-DB-040 en `db/schema.sql`: columnas
  nuevas AL FINAL de la tabla (los `ADD COLUMN` de Alembic van al final) —
  `model_catalog.context_tokens/max_output_tokens` (spec 018) y
  `agents.profile_key` (spec 020, lane hermano, mismo patrón).
- `db/tests/test_rls_coverage.py` actualizado a 16 policy_keys (12 + 4 de la
  migración 0011 del lane 017) — el test quedó stale tras ese lane.
- T7: `scripts/evidence_model_access_maas.py` (21 checks) + capturas en
  `docs/evidencia/model-access-maas/`.

## Re-verificación en vivo (2026-10-26 12:19 CST, intento 15)

- **21/21 PASS** — log completo en `docs/evidencia/model-access-maas/04-evidence-r15-reuse.log`
  (+ README con la cadena E1–E7 y la nota de la corrida).
- Corrida en modo `--reuse` (nuevo) contra `model-access-maas-retry144`: la re-validación con
  slug fresco quedó bloqueada por una **regresión ajena** (lane auth UX t_1844fb06, comentario
  #65 en su card): realms provisionados desde el rebuild de la imagen API (~11:43 CST) tienen el
  browser flow roto → authorize 400 sin login. El tenant usado es pre-rebuild (flow sano), con
  DB restaurada al estado post-provisioning y TOTP re-enrolado; login y invocación 100% reales.
- Tests re-ejecutados en esta corrida (DB aislada `TRUST_TEST_DB`):
  `test_model_access.py` + `test_catalogs.py` + `test_governance.py` = 20/20;
  `test_policies_api.py` + `test_governance_bundle.py` = 30/30;
  `db/tests` = 178 passed + paridad schema 2/2 (`TRUST_DB_CONTAINER=trust-agent-db`).
  2 fallos en db/tests (`policy_keys WHERE invariable` espera 4, hay 3) son expectativas stale
  de la migración 0014 de la lane auth UX (`require_mfa` ahora org-tunable) — reportados en su
  card, no son de esta spec.
- UI verificada en la imagen viva del console (rebuild 17:26 UTC): el bundle sirve
  `models-page.tsx` con `usdPerMillion` y el bloque "Governance policy" (T4).
- Pendiente cuando la lane auth UX repare el flow: re-validar con slug fresco
  (`--slug <nuevo>`, sin `--password`) para dejar la cadena con provisioning nuevo incluido.

## §5 — Sincronía catálogo ↔ /models vivo (D-018-07, GAP M-2 / t_bf7eff20, 2026-10-27)

- [x] Re-verificación en vivo del catálogo contra `GET {MAAS_ENDPOINT}/models`
      (2026-10-27T20:55Z: 6 modelos SIN qwen3-32b — 404 `ModelArts.81009`;
      NUEVO `deepseek-v4.1-flash-1017` con doc oficial ya publicada).
- [x] Migración `0026_retire_qwen3_32b` (guard): qwen3-32b → stale
      (conserva model_access/historia) + alta de deepseek-v4.1-flash-1017
      con metadatos oficiales (30/120 ¢/M P1, 1M/384K). Paridad schema.sql.
- [x] Migración `0028_nullable_catalog_prices` (guard): precios nullable —
      el alta automática inserta NULL = coste desconocido hasta sellado.
- [x] `refresh_central` reconcilia contra el upstream vivo (retiro / alta +
      registro en gateway / restore); sin TRUST_MAAS_* degrada al chequeo de
      routing (nunca fail-open).
- [x] Job periódico `model.catalog_refresh` para TODOS los tenants activos
      (verificado en vivo: "enqueued for 87 tenant(s)").
- [x] `litellm-real/config.yaml`: fuera qwen3-32b, dentro 1017; proxy real
      reiniciado (GET /v1/models = deployments vivos).
- [x] Tests: `test_central_sync_against_live_upstream` (nuevo, retiro/alta/
      restore contra doble upstream) + actualización de test_catalogs,
      test_model_access, test_global_refresh_and_central_sync,
      test_periodic_refresh_job_idempotent (24 passed) + db/tests 51 passed
      (paridad schema 2/2).
- [x] Evidencia en vivo AC-018-07: `scripts/evidence_catalog_sync.py` →
      **27 PASS, 0 FAIL** (2026-10-27T21:32Z, slug catalog-sync-1027c;
      transición restored=1 capturada en la corrida 1027a) —
      `docs/evidencia/pilar2/gap-m2-catalog-sync.md` + log.
- [x] Skill `huawei-maas-models` v1.1.0: catálogo enmendado + lecciones
      (deriva bidireccional; listado ≠ invocable: 403 ModelArts.81004).
- [ ] PENDIENTE HUMANO: habilitar en la consola MaaS el acceso de la key
      "Inforum" a `deepseek-v4.1-flash-1017` (hoy 403 ModelArts.81004).
