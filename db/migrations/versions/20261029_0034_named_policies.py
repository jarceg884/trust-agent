"""Migración 0034: named_policies (spec 041, políticas nombradas — visión Miller).

Una política nombrada es un PRESET de policy keys (D-41-01): la asignación
MATERIALIZA el mapa ``key → value`` vía ``policies.upsert`` existente en una
transacción atómica, y la tabla ``policies`` sigue siendo la ÚNICA verdad
para combinator/matrix/effective/bundle JWS. El engine NO se toca.

Piezas:

  1. ``named_policies`` — name + description + definition (jsonb, mapa
     key→value sobre las 44 keys no invariables) + ``source_template``
     (``'builtin:<slug>'`` para las plantillas sembradas, D-41-08) + UNIQUE
     (tenant_id, name) + índice parcial UNIQUE (tenant_id, source_template)
     que hace el seed idempotente POR SLUG.
  2. ``named_policy_assignments`` — una fila por (política, destinatario):
     level org/dept/user + ``applied_keys`` (snapshot de ownership, D-41-05)
     + ``applied_at`` (marcador de stale vs policy.updated_at, D-41-06) +
     UNIQUE por (named_policy_id, level, dept, user) con COALESCE (mismo
     truco que policies_scope_uidx para los NULL de org).
  3. RLS por tenant_id (patrón fail-closed + FORCE de la sección 7) y
     grants a trust_app.
  4. Acciones de audit ``named_policy.*`` (enum versionado, extensión por
     datos — FK de audit_log.action).
  5. Backfill: las 3 plantillas del contrato canónico
     (specs/041-named-policies/named-policy-builder.json, sha256
     207ab6b5…) se siembran para los tenants EXISTENTES; el provisioning
     nuevo las siembra desde platform/app/governance/named_policies.py
     (mismo contrato, leído del package data). Las definitions van
     EMBEBIDAS aquí (una migración no debe leer archivos del árbol a
     runtime); el test de plataforma test_named_policies verifica que la
     definición sembrada == la del contrato.

Paridad schema.sql: sección 25 (exigida por db/tests/test_schema_parity.py).
"""
import json

from alembic import op
from sqlalchemy import text as sa_text

revision = "0034_named_policies"
down_revision = "0033_departments_onboarding"
branch_labels = None
depends_on = None

# Plantillas del contrato canónico de la spec 041 (copia literal — el test
# de plataforma exige igualdad contra named_policy_builder.json).
_TEMPLATES = [
    {
        "slug": "solo-lectura-web",
        "name": "Solo lectura web",
        "description": (
            "El agente puede buscar y leer información en internet y "
            "archivos, pero no ejecutar comandos, no escribir archivos ni "
            "usar conectores. Apto para investigación y consulta."
        ),
        "definition": {
            "allowed_tools": ["web_search", "read_file", "governance_status"],
            "allowed_terminal_backends": [],
            "web_domain_policy": {"mode": "open", "domains": []},
            "approval_mode": "manual",
            "allow_credential_vault": False,
            "allow_scheduled_tasks": False,
            "allow_voice_mode": False,
            "allow_bot_mode": False,
        },
    },
    {
        "slug": "desarrollador",
        "name": "Desarrollador",
        "description": (
            "El agente puede usar terminal (contenedor Docker y equipo "
            "local), leer y escribir archivos, buscar en internet y "
            "programar tareas, con sandbox de escritura y aprobación "
            "inteligente."
        ),
        "definition": {
            "allowed_tools": [
                "web_search", "read_file", "write_file", "governance_status",
            ],
            "allowed_terminal_backends": ["docker", "local"],
            "require_write_sandbox": True,
            "approval_mode": "smart",
            "allow_shell_hooks": True,
            "web_domain_policy": {"mode": "open", "domains": []},
            "allowed_skills": ["*"],
            "allow_scheduled_tasks": True,
        },
    },
    {
        "slug": "restringido-total",
        "name": "Restringido total",
        "description": (
            "El agente solo conversa y reporta su estado de gobernanza: sin "
            "internet, sin terminal, sin archivos, sin conectores, sin "
            "mensajería, sin automatización. Máximo encierro."
        ),
        "definition": {
            "allowed_tools": ["governance_status"],
            "allowed_terminal_backends": [],
            "allowed_connectors": [],
            "allowed_connections": [],
            "allowed_skills": [],
            "web_domain_policy": {"mode": "allowlist", "domains": []},
            "messaging_permissions": {},
            "approval_mode": "manual",
            "max_daily_sessions": 2,
            "allow_credential_vault": False,
            "allow_payment_autofill": False,
            "allow_voice_mode": False,
            "allow_bot_mode": False,
            "allow_goal_mode": False,
            "allow_scheduled_tasks": False,
            "allow_recurring_prompts": False,
        },
    },
]


def upgrade() -> None:
    # --- 1. named_policies ---------------------------------------------------
    op.execute(
        """
        CREATE TABLE named_policies (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id      uuid NOT NULL REFERENCES tenants(id),
          name           text NOT NULL,
          description    text,
          -- Mapa key → value sobre las 44 keys no invariables del catálogo
          -- tipado (validado por la API con el MISMO _validate_value del
          -- upsert por key; aquí no puede vivir un FK).
          definition     jsonb NOT NULL,
          -- 'builtin:<slug>' para las plantillas sembradas (badge UI);
          -- NULL para las políticas del admin.
          source_template text,
          created_by     uuid,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX named_policies_tenant_name_uidx "
        "  ON named_policies (tenant_id, name)"
    )
    # Seed idempotente POR SLUG (D-41-08): re-correr el backfill/provisioning
    # no duplica; si el admin BORRA la plantilla, el slug queda libre (las
    # plantillas son políticas comunes, editables y borrables).
    op.execute(
        "CREATE UNIQUE INDEX named_policies_template_uidx "
        "  ON named_policies (tenant_id, source_template) "
        "  WHERE source_template IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX named_policies_tenant_idx ON named_policies (tenant_id)"
    )

    # --- 2. named_policy_assignments ----------------------------------------
    op.execute(
        """
        CREATE TABLE named_policy_assignments (
          id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id        uuid NOT NULL REFERENCES tenants(id),
          named_policy_id  uuid NOT NULL REFERENCES named_policies(id)
                              ON DELETE CASCADE,
          level            text NOT NULL CHECK (level IN ('org','dept','user')),
          department_id    uuid,
          user_id          uuid,
          -- Snapshot de ownership (D-41-05): el mapa key→value efectivamente
          -- escrito por ESTA asignación. Desasignar solo toca las keys cuyo
          -- valor actual siga siendo el del snapshot.
          applied_keys     jsonb NOT NULL,
          applied_at       timestamptz NOT NULL DEFAULT now(),
          created_by       uuid,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT named_policy_assignments_scope_check CHECK (
            (level = 'org'  AND department_id IS NULL     AND user_id IS NULL) OR
            (level = 'dept' AND department_id IS NOT NULL AND user_id IS NULL) OR
            (level = 'user' AND department_id IS NOT NULL AND user_id IS NOT NULL)
          ),
          FOREIGN KEY (tenant_id, department_id)
            REFERENCES departments (tenant_id, id),
          FOREIGN KEY (tenant_id, user_id)
            REFERENCES users (tenant_id, id)
        )
        """
    )
    # UNIQUE por (política, destinatario) — los NULL de org no colisionan
    # (mismo truco que policies_scope_uidx).
    op.execute(
        "CREATE UNIQUE INDEX named_policy_assignments_scope_uidx "
        "  ON named_policy_assignments (named_policy_id, level, "
        "    COALESCE(department_id, '00000000-0000-0000-0000-000000000000'::uuid), "
        "    COALESCE(user_id,       '00000000-0000-0000-0000-000000000000'::uuid))"
    )
    op.execute(
        "CREATE INDEX named_policy_assignments_tenant_idx "
        "  ON named_policy_assignments (tenant_id)"
    )
    op.execute(
        "CREATE INDEX named_policy_assignments_policy_idx "
        "  ON named_policy_assignments (named_policy_id)"
    )

    # updated_at automáticas (misma función set_updated_at de la sección 6).
    op.execute(
        "CREATE TRIGGER named_policies_set_updated_at "
        "  BEFORE UPDATE ON named_policies "
        "  FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.execute(
        "CREATE TRIGGER named_policy_assignments_set_updated_at "
        "  BEFORE UPDATE ON named_policy_assignments "
        "  FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # --- 3. RLS (patrón fail-closed + FORCE de la sección 7) ----------------
    op.execute(
        """
        DO $$
        DECLARE
          t text;
          tables text[] := ARRAY['named_policies','named_policy_assignments'];
        BEGIN
          FOREACH t IN ARRAY tables
          LOOP
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
            EXECUTE format($p$
              CREATE POLICY tenant_isolation ON public.%I
                FOR ALL TO trust_app
                USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            $p$, t);
          END LOOP;
        END $$;
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "named_policies, named_policy_assignments TO trust_app"
    )
    # El deprovisioning (spec 031) borra las filas del tenant como
    # trust_migrator (BYPASSRLS): sin este grant revienta con
    # InsufficientPrivilege (mismo patrón que la migración 0032 y el
    # GRANT healing de la sección 23 de schema.sql).
    op.execute(
        "GRANT ALL PRIVILEGES ON TABLE "
        "named_policies, named_policy_assignments TO trust_migrator"
    )

    # --- 4. Acciones de audit (FK de audit_log.action) -----------------------
    op.execute(
        """
        INSERT INTO catalog.audit_actions (action, description) VALUES
          ('named_policy.created',
           'Named policy created (preset of policy keys, spec 041).'),
          ('named_policy.updated',
           'Named policy edited; existing assignments become stale until reapplied.'),
          ('named_policy.deleted',
           'Named policy deleted (only without active assignments, or after cascade unassign).'),
          ('named_policy.assigned',
           'Named policy assigned to org/dept/user: keys materialized via policies.upsert in one transaction.'),
          ('named_policy.unassigned',
           'Named policy unassigned: only snapshot-owned keys deactivated; manually edited keys kept.'),
          ('named_policy.reapplied',
           'Stale assignment re-materialized (best-effort per recipient).')
        ON CONFLICT (action) DO NOTHING
        """
    )

    # --- 5. Backfill de plantillas para los tenants existentes (D-41-08) ----
    # Idempotente por (tenant, slug): el índice parcial UNIQUE + NOT EXISTS
    # hace que re-ejecutar el seed no duplique.
    bind = op.get_bind()
    for tpl in _TEMPLATES:
        bind.execute(
            sa_text(
                """
                INSERT INTO named_policies
                  (tenant_id, name, description, definition, source_template)
                SELECT t.id, :name, :description,
                       CAST(:definition AS jsonb), :source_template
                FROM tenants t
                WHERE NOT EXISTS (
                  SELECT 1 FROM named_policies np
                  WHERE np.tenant_id = t.id
                    AND np.source_template = :source_template
                )
                """
            ),
            {
                "name": tpl["name"],
                "description": tpl["description"],
                "definition": json.dumps(tpl["definition"]),
                "source_template": f"builtin:{tpl['slug']}",
            },
        )
        # Audit del backfill (actor = provider, mismo papel del
        # provisioning). audit_log es append-only y el trigger de cadena
        # corre igual para trust_migrator (BYPASSRLS); el payload lleva
        # las keys sembradas.
        bind.execute(
            sa_text(
                """
                INSERT INTO audit_log
                  (tenant_id, actor_role, action, target_type, target_id,
                   payload)
                SELECT np.tenant_id, 'provider', 'named_policy.created',
                       'named_policy', np.id,
                       CAST(:payload AS jsonb)
                FROM named_policies np
                WHERE np.source_template = :source_template
                """
            ),
            {
                "source_template": f"builtin:{tpl['slug']}",
                "payload": json.dumps(
                    {
                        "name": tpl["name"],
                        "source_template": f"builtin:{tpl['slug']}",
                        "seed": True,
                        "keys": sorted(tpl["definition"].keys()),
                    }
                ),
            },
        )


def downgrade() -> None:
    # Reverso exacto. audit_log es append-only: el trigger de rechazo admite
    # DELETE solo con la excepción documentada app.audit_purge='on' (misma
    # vía del job de retención y del deprovisioning) — sin purgar los
    # eventos named_policy.* el DELETE de catalog.audit_actions revienta
    # por la FK de audit_log.action.
    op.execute("SELECT set_config('app.audit_purge', 'on', false)")
    op.execute("DELETE FROM audit_log WHERE action LIKE 'named_policy.%'")
    op.execute("SELECT set_config('app.audit_purge', '', false)")
    op.execute(
        "DELETE FROM catalog.audit_actions WHERE action LIKE 'named_policy.%'"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON named_policy_assignments")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON named_policies")
    op.execute("DROP TABLE IF EXISTS named_policy_assignments")
    op.execute("DROP TABLE IF EXISTS named_policies")
