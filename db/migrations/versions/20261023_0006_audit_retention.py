"""Audit retention/purge evolution (spec 013 D-013-04, REQ-AUD-006).

Revision ID: 0006_audit_retention
Revises: 0005_org_endpoints
Create Date: 2026-10-23

DDL canónico: db/schema.sql sección 11 (embebida verbatim aquí para que la
vía Alembic produzca el mismo estado — paridad pg_dump exigida por
db/tests/test_schema_parity.py). Nueva tabla audit_chain_checkpoint +
RLS, excepción de purga en el trigger de inmutabilidad, continuidad de
cadena vía checkpoint en el trigger de INSERT, verificación desde
checkpoint y función job audit_purge_expired. Una acción nueva de audit
(audit.retention_updated) sembrada como datos.
"""
from alembic import op

revision = "0006_audit_retention"
down_revision = "0005_org_endpoints"
branch_labels = None
depends_on = None

SCHEMA_SQL = r"""
-- 11.1 Acción de audit nueva (enum versionado, extensión por datos;
--      `audit.purged` ya existía en el catálogo desde 0001).
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('audit.retention_updated', 'Tenant audit retention setting updated (minimum 365 days).')
ON CONFLICT (action) DO NOTHING;

-- 11.2 Checkpoint de cadena por tenant (REQ-AUD-006).
CREATE TABLE IF NOT EXISTS audit_chain_checkpoint (
  tenant_id   uuid PRIMARY KEY REFERENCES tenants(id),
  last_seq    bigint NOT NULL,
  last_hash   char(64) NOT NULL,
  purged_rows bigint NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE audit_chain_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_chain_checkpoint FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_chain_checkpoint
  FOR ALL TO trust_app
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
GRANT SELECT ON TABLE audit_chain_checkpoint TO trust_app;
-- La tabla nace DESPUÉS del grant blanket de la sección 8, pero los
-- default privileges le dan DML completo: revocar explícito — la app es
-- SELECT-only aquí (las escrituras son del job de purga, migrator).
REVOKE INSERT, UPDATE, DELETE ON TABLE audit_chain_checkpoint FROM trust_app;
GRANT ALL PRIVILEGES ON TABLE audit_chain_checkpoint TO trust_migrator;

-- 11.3 Inmutabilidad con excepción de purga autorizada (migrator + flag).
CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE'
     AND current_user = 'trust_migrator'
     AND COALESCE(current_setting('app.audit_purge', true), '') = 'on' THEN
    RETURN OLD;  -- authorized retention purge (REQ-AUD-006)
  END IF;
  RAISE EXCEPTION 'audit_log is append-only: % is prohibited (id %)', TG_OP, COALESCE(OLD.id, NEW.id)
    USING ERRCODE = 'raise_exception';
END $$;

-- 11.4 Continuidad de cadena a través de una purga total.
CREATE OR REPLACE FUNCTION audit_log_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prev_hash text;
  v_prev_seq  bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('audit:' || NEW.tenant_id::text, 0));
  SELECT hash, seq INTO v_prev_hash, v_prev_seq
    FROM audit_log
    WHERE tenant_id = NEW.tenant_id
    ORDER BY id DESC
    LIMIT 1;
  IF NOT FOUND THEN
    SELECT last_hash, last_seq INTO v_prev_hash, v_prev_seq
      FROM audit_chain_checkpoint WHERE tenant_id = NEW.tenant_id;
  END IF;
  NEW.seq       := COALESCE(v_prev_seq, 0) + 1;
  NEW.hash_prev := COALESCE(v_prev_hash, repeat('0', 64));
  NEW.hash      := encode(sha256(convert_to(NEW.hash_prev || '|' || audit_canonical_payload(NEW), 'utf8')), 'hex');
  RETURN NEW;
END $$;

-- 11.5 Verificación desde el checkpoint (si existe) en vez de la génesis.
CREATE OR REPLACE FUNCTION audit_verify_chain(p_tenant uuid)
RETURNS TABLE (status text, seq bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r        audit_log%ROWTYPE;
  v_prev   text;
  v_seq    bigint;
BEGIN
  SELECT last_hash, last_seq INTO v_prev, v_seq
    FROM audit_chain_checkpoint WHERE tenant_id = p_tenant;
  IF NOT FOUND THEN
    v_prev := repeat('0', 64);
    v_seq  := 0;
  END IF;
  FOR r IN SELECT * FROM audit_log WHERE tenant_id = p_tenant ORDER BY id LOOP
    IF r.seq <> v_seq + 1 OR r.hash_prev <> v_prev
       OR r.hash <> encode(sha256(convert_to(r.hash_prev || '|' || audit_canonical_payload(r), 'utf8')), 'hex') THEN
      RETURN QUERY SELECT 'broken'::text, r.seq;
      RETURN;
    END IF;
    v_prev := r.hash;
    v_seq  := r.seq;
  END LOOP;
  RETURN QUERY SELECT 'ok'::text, NULL::bigint;
END $$;

-- 11.6 Job de purga (REQ-AUD-006): solo ejecutable por trust_migrator.
CREATE OR REPLACE FUNCTION audit_purge_expired(p_tenant uuid, p_retention_days integer DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  v_retention integer;
  v_cutoff    timestamptz;
  v_last      audit_log%ROWTYPE;
  v_count     bigint;
BEGIN
  IF p_retention_days IS NOT NULL AND p_retention_days < 365 THEN
    RAISE EXCEPTION 'audit retention floor is 365 days (REQ-AUD-006)'
      USING ERRCODE = 'check_violation';
  END IF;
  SELECT retention_days INTO v_retention FROM tenants WHERE id = p_tenant;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown tenant %', p_tenant USING ERRCODE = 'foreign_key_violation';
  END IF;
  v_cutoff := now() - (COALESCE(p_retention_days, v_retention) || ' days')::interval;
  PERFORM set_config('app.tenant_id', p_tenant::text, true);
  PERFORM set_config('app.audit_purge', 'on', true);
  PERFORM pg_advisory_xact_lock(hashtextextended('audit:' || p_tenant::text, 0));
  SELECT * INTO v_last FROM audit_log
    WHERE tenant_id = p_tenant AND ts < v_cutoff
    ORDER BY id DESC LIMIT 1;
  IF NOT FOUND THEN
    RETURN 0;
  END IF;
  DELETE FROM audit_log WHERE tenant_id = p_tenant AND ts < v_cutoff;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  INSERT INTO audit_chain_checkpoint (tenant_id, last_seq, last_hash, purged_rows)
    VALUES (p_tenant, v_last.seq, v_last.hash, v_count)
    ON CONFLICT (tenant_id) DO UPDATE SET
      last_seq = EXCLUDED.last_seq,
      last_hash = EXCLUDED.last_hash,
      purged_rows = audit_chain_checkpoint.purged_rows + EXCLUDED.purged_rows,
      updated_at = now();
  INSERT INTO audit_log (tenant_id, actor_role, action, target_type, target_id, payload)
    VALUES (p_tenant, 'system', 'audit.purged', 'tenant', p_tenant::text,
            jsonb_build_object('purged_rows', v_count,
                               'retention_days', COALESCE(p_retention_days, v_retention),
                               'cutoff', v_cutoff));
  RETURN v_count;
END $$;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    # La purga es irreversible por diseño (append-only + checkpoint); el
    # downgrade no elimina la tabla de checkpoints ni las acciones del
    # catálogo (audit_log.action tiene FK y podría haber eventos emitidos).
    pass
