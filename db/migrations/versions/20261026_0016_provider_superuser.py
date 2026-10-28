"""Spec 025 (mandato Miller): super-user del Provider Console en la BD.

El operador del proveedor es el ÚNICO actor fuera del multi-tenant: vive en
el plano proveedor ``ops`` (sin RLS por diseño, D-DB-01 — misma razón que
``ops.login_throttle``). Tres objetos nuevos:

- ``ops.provider_operators`` — username único, password PHC argon2id (jamás
  plaintext), status active/disabled, secret TOTP cifrado en reposo
  (envelope AES-256-GCM del vault, KEK fuera de la BD) nullable,
  ``must_change_password`` (required action del primer login).
- ``ops.provider_sessions`` — sesiones del operador en PostgreSQL (misma
  infra que ``public.sessions``: token opaco, solo sha256 del secret en BD,
  nada en memoria → sobreviven reinicios del API).
- ``ops.provider_audit`` — audit append-only del plano proveedor con hash
  chain GLOBAL (mismo patrón del trigger de ``audit_log`` pero sin
  partición por tenant) + trigger de inmutabilidad. Los eventos del
  operador (provider.login, provider.login_denied, provider.mfa_enrolled,
  provider.mfa_disabled, provider.password_changed) no pertenecen a ningún
  tenant, así que ``audit_log`` (tenant_id NOT NULL + FK) no puede
  alojarlos. ``action`` lleva FK a ``catalog.audit_actions`` (data-driven,
  I10): las 5 acciones se añaden al catálogo.

El SQL va a columna 0 (patrón de 0001_initial): el test de paridad compara
pg_dump de ambas vías y los cuerpos de función deben ser byte-idénticos a
db/schema.sql.

Paridad canónica: db/schema.sql §3 (catalog.audit_actions) y §4 (ops),
exigida por db/tests/test_schema_parity.py.
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_provider_superuser"
down_revision = "0015_tenant_self_signup"
branch_labels = None
depends_on = None

ACTIONS_SQL = """
INSERT INTO catalog.audit_actions (action, description) VALUES
  ('provider.login',           'Provider operator signed in (username+password[+TOTP]).'),
  ('provider.login_denied',    'Provider operator login denied (bad credentials, disabled or bad TOTP).'),
  ('provider.password_changed','Provider operator changed their password (first-login required action or rotation).'),
  ('provider.mfa_enrolled',    'Provider operator enrolled TOTP MFA (verified before activation).'),
  ('provider.mfa_disabled',    'Provider operator disabled TOTP MFA (password+code confirmed).')
ON CONFLICT (action) DO NOTHING;
"""

OPERATORS_SQL = """
CREATE TABLE IF NOT EXISTS ops.provider_operators (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username             text NOT NULL UNIQUE,
  password_hash        text NOT NULL,
  status               text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','disabled')),
  must_change_password boolean NOT NULL DEFAULT true,
  totp_secret_enc      bytea,
  totp_dek_enc         bytea,
  totp_nonce           bytea,
  totp_key_id          text,
  totp_active          boolean NOT NULL DEFAULT false,
  totp_enrolled_at     timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
"""

SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS ops.provider_sessions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operator_id  uuid NOT NULL REFERENCES ops.provider_operators(id),
  jti          text NOT NULL UNIQUE,
  status       text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','revoked','expired')),
  mfa_verified boolean NOT NULL DEFAULT false,
  ip           inet,
  user_agent   text,
  expires_at   timestamptz NOT NULL,
  last_seen_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS provider_sessions_operator_idx
  ON ops.provider_sessions (operator_id, status);
"""

AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS ops.provider_audit (
  id          bigserial PRIMARY KEY,
  seq         bigint NOT NULL,
  action      text NOT NULL REFERENCES catalog.audit_actions(action),
  operator_id uuid,
  username    text,
  ip          inet,
  user_agent  text,
  payload     jsonb NOT NULL DEFAULT '{}',
  ts          timestamptz NOT NULL DEFAULT now(),
  hash_prev   char(64) NOT NULL,
  hash        char(64) NOT NULL
);
"""

# Cuerpos de función byte-idénticos a db/schema.sql §4 (paridad pg_dump).
FUNCS_SQL = r"""
CREATE OR REPLACE FUNCTION ops.provider_audit_canonical_payload(
    rec ops.provider_audit) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT concat_ws('|',
    rec.seq::text,
    rec.action,
    COALESCE(rec.operator_id::text, ''),
    COALESCE(rec.username, ''),
    COALESCE(rec.ip::text, ''),
    COALESCE(rec.user_agent, ''),
    rec.payload::text,
    extract(epoch FROM rec.ts)::text
  );
$$;

CREATE OR REPLACE FUNCTION ops.provider_audit_before_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_prev_hash text;
  v_prev_seq  bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(
    hashtextextended('provider-audit', 0));
  SELECT hash, seq INTO v_prev_hash, v_prev_seq
    FROM ops.provider_audit
    ORDER BY id DESC
    LIMIT 1;
  NEW.seq       := COALESCE(v_prev_seq, 0) + 1;
  NEW.hash_prev := COALESCE(v_prev_hash, repeat('0', 64));
  NEW.hash      := encode(sha256(convert_to(
    NEW.hash_prev || '|' ||
    ops.provider_audit_canonical_payload(NEW), 'utf8')), 'hex');
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS provider_audit_chain_trigger ON ops.provider_audit;
CREATE TRIGGER provider_audit_chain_trigger
  BEFORE INSERT ON ops.provider_audit
  FOR EACH ROW EXECUTE FUNCTION ops.provider_audit_before_insert();

CREATE OR REPLACE FUNCTION ops.provider_audit_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION
    'ops.provider_audit is append-only: % is prohibited (id %)',
    TG_OP, COALESCE(OLD.id, NEW.id)
    USING ERRCODE = 'raise_exception';
END $$;

DROP TRIGGER IF EXISTS provider_audit_immutable_trigger ON ops.provider_audit;
CREATE TRIGGER provider_audit_immutable_trigger
  BEFORE UPDATE OR DELETE ON ops.provider_audit
  FOR EACH ROW EXECUTE FUNCTION ops.provider_audit_reject_mutation();

CREATE OR REPLACE FUNCTION ops.provider_audit_verify_chain()
RETURNS TABLE (status text, seq bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r ops.provider_audit%ROWTYPE;
  v_prev text := repeat('0', 64);
  v_seq  bigint := 0;
BEGIN
  FOR r IN SELECT * FROM ops.provider_audit ORDER BY id LOOP
    IF r.seq <> v_seq + 1 OR r.hash_prev <> v_prev
       OR r.hash <> encode(sha256(convert_to(
            r.hash_prev || '|' ||
            ops.provider_audit_canonical_payload(r), 'utf8')),
            'hex') THEN
      RETURN QUERY SELECT 'broken'::text, r.seq;
      RETURN;
    END IF;
    v_prev := r.hash;
    v_seq  := r.seq;
  END LOOP;
  RETURN QUERY SELECT 'ok'::text, NULL::bigint;
END $$;
"""

TRIGGERS_SQL = """
CREATE TRIGGER provider_operators_set_updated_at
  BEFORE UPDATE ON ops.provider_operators
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER provider_sessions_set_updated_at
  BEFORE UPDATE ON ops.provider_sessions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

COMMENTS_SQL = """
COMMENT ON TABLE ops.provider_operators IS 'Provider super-users (spec 025, mandate Miller): the ONLY actor outside the multi-tenant scope. Provider plane: no tenant_id, no RLS (D-DB-01). password_hash is a PHC argon2id string; the TOTP secret is stored envelope-encrypted (AES-256-GCM, KEK never in the DB) and is NULL until enrollment.';
COMMENT ON TABLE ops.provider_sessions IS 'Provider operator sessions in PostgreSQL (spec 025 D-PS-04): opaque token pst1.<id>.<secret>, only sha256(secret) stored (jti). Same infrastructure as public.sessions but in the provider plane (no tenant scope). Nothing in process memory: sessions survive API restarts by construction.';
COMMENT ON TABLE ops.provider_audit IS 'Append-only audit for provider-plane events (spec 025 D-PS-07): operator access events that belong to no tenant. Global hash chain (same trigger pattern as audit_log, single sequence); UPDATE/DELETE rejected by trigger.';
"""

GRANTS_SQL = """
GRANT SELECT, INSERT, UPDATE ON TABLE ops.provider_operators TO trust_app;
GRANT SELECT, INSERT, UPDATE ON TABLE ops.provider_sessions TO trust_app;
GRANT SELECT, INSERT ON TABLE ops.provider_audit TO trust_app;
GRANT USAGE ON SEQUENCE ops.provider_audit_id_seq TO trust_app;
GRANT ALL ON TABLE ops.provider_operators TO trust_migrator;
GRANT ALL ON TABLE ops.provider_sessions TO trust_migrator;
GRANT ALL ON TABLE ops.provider_audit TO trust_migrator;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE ops.provider_audit_id_seq TO trust_migrator;
"""


def upgrade() -> None:
    op.execute(sa.text(ACTIONS_SQL))
    op.execute(sa.text(OPERATORS_SQL))
    op.execute(sa.text(SESSIONS_SQL))
    op.execute(sa.text(AUDIT_SQL))
    op.execute(sa.text(FUNCS_SQL))
    op.execute(sa.text(TRIGGERS_SQL))
    op.execute(sa.text(COMMENTS_SQL))
    op.execute(sa.text(GRANTS_SQL))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS ops.provider_audit_verify_chain()"))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS ops.provider_audit_reject_mutation()"))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS ops.provider_audit_before_insert()"))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS ops.provider_audit_canonical_payload("
        "rec ops.provider_audit)"))
    op.execute(sa.text("DROP TABLE IF EXISTS ops.provider_audit"))
    op.execute(sa.text("DROP TABLE IF EXISTS ops.provider_sessions"))
    op.execute(sa.text("DROP TABLE IF EXISTS ops.provider_operators"))
    op.execute(sa.text("""
        DELETE FROM catalog.audit_actions WHERE action IN (
          'provider.login',
          'provider.login_denied',
          'provider.password_changed',
          'provider.mfa_enrolled',
          'provider.mfa_disabled'
        );
    """))
