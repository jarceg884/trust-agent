"""AC-DB-030/031/033 + AC-DB-001/002/003 — Cobertura de RLS, roles y grants.

Introspección del schema real: todas las tablas de negocio con tenant_id,
RLS ENABLE+FORCE, política única para trust_app, default deny, índices
tenant-first, grants correctos y catálogos como datos.
"""
import psycopg.errors

from conftest import BUSINESS_TABLES


def test_every_business_table_has_tenant_id_rls_forced_and_policy(admin_conn):
    cur = admin_conn.cursor()
    for table in BUSINESS_TABLES:
        cur.execute(
            """
            SELECT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'public' AND table_name = %s
                             AND column_name = 'tenant_id'),
                   c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policy p
                           WHERE p.polrelid = c.oid
                             AND p.polname = 'tenant_isolation'
                             AND p.polroles @> ARRAY[(SELECT oid FROM pg_roles WHERE rolname = 'trust_app')])
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = %s
            """,
            (table, table),
        )
        has_tenant_id, rls_on, rls_forced, has_policy = cur.fetchone()
        assert has_tenant_id, f"{table}: falta columna tenant_id"
        assert rls_on, f"{table}: RLS no activada"
        assert rls_forced, f"{table}: falta FORCE ROW LEVEL SECURITY"
        assert has_policy, f"{table}: falta política tenant_isolation para trust_app"


def test_no_unexpected_public_tables_without_rls(admin_conn):
    """Toda tabla de public (salvo alembic_version) es de negocio y está cubierta."""
    cur = admin_conn.cursor()
    cur.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    )
    tables = {r[0] for r in cur.fetchall()}
    assert tables - {"alembic_version"} == set(BUSINESS_TABLES), (
        f"Tablas inesperadas o faltantes en public: {tables}"
    )


def test_catalog_and_ops_are_provider_plane_by_design(admin_conn):
    """catalog/ops: sin tenant_id y sin RLS (plano del proveedor — D-DB-01)."""
    cur = admin_conn.cursor()
    cur.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('catalog','ops') AND c.relkind = 'r'"
    )
    provider_tables = [r[0] for r in cur.fetchall()]
    assert set(provider_tables) == {
        "roles", "permissions", "role_permissions", "policy_keys",
        "model_catalog", "connector_catalog", "audit_actions", "spend_cursor",
        "login_throttle", "spend_rejections",
        # Spec 025 (mandato Miller): super-user del Provider Console —
        # operador, sesiones y audit del plano proveedor (D-PS-01/04/07).
        "provider_operators", "provider_sessions", "provider_audit",
        # Spec 015 v1.3.0 (ADR-019): estado de los auth dances del panel
        # (TOTP enrollment + proof handle) — pre-autenticación, sin RLS.
        "auth_dances",
        # Spec 036 (D-036-01): proveedores de modelos operados por el
        # provider + habilitación por cliente — plano provider (patrón
        # ops.provider_operators), sin RLS; el aislamiento por tenant
        # vive en catalog.model_catalog (AC-036-07).
        "model_providers",
        "model_provider_tenants",
        }
    # ops.spend_rejections (QA H2, D-DB-15) SÍ lleva una columna tenant_id:
    # es una referencia forense a un tenant que NO existe (sin FK), no dato
    # de negocio de un tenant — se verifica solo "sin RLS" más abajo.
    # ops.auth_dances (ADR-019): mismo criterio — el tenant_id es una
    # referencia PRE-autenticación (el mint aún no ocurrió), no dato de
    # negocio; sin RLS por diseño (D-DB-01/D-DB-14).
    targets = [(("catalog"), t) for t in provider_tables
               if t not in ("spend_cursor", "login_throttle", "spend_rejections",
                            "provider_operators", "provider_sessions",
                            "provider_audit", "model_catalog", "auth_dances",
                            "model_providers", "model_provider_tenants")]
    targets.append(("ops", "spend_cursor"))
    targets.append(("ops", "login_throttle"))
    targets.append(("ops", "provider_operators"))
    targets.append(("ops", "provider_sessions"))
    targets.append(("ops", "provider_audit"))
    # Spec 036 (D-036-01): ops.model_providers — plano provider puro: sin
    # tenant_id y sin RLS (la key vive en envelope; el acceso es solo vía
    # API del provider con permiso providers.manage).
    targets.append(("ops", "model_providers"))
    for schema, table in targets:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = 'tenant_id'), "
            "c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s",
            (schema, table, schema, table),
        )
        has_tenant_id, rls_on = cur.fetchone()
        assert not has_tenant_id, f"{schema}.{table}: no debería tener tenant_id"
        assert not rls_on, f"{schema}.{table}: no debería tener RLS"
    # Spec 032 (discovery dinámico): catalog.model_catalog SÍ lleva tenant_id
    # + RLS — sus filas byok son dato de negocio del tenant; las centrales
    # (tenant_id NULL) siguen visibles para todos (migración 0024, §19.2).
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'catalog' AND table_name = 'model_catalog' "
        "AND column_name = 'tenant_id'), c.relrowsecurity, c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'catalog' AND c.relname = 'model_catalog'"
    )
    has_tenant_id, rls_on, forced = cur.fetchone()
    assert has_tenant_id, "catalog.model_catalog: debe tener tenant_id (spec 032)"
    assert rls_on and forced, "catalog.model_catalog: debe tener RLS FORCED (spec 032)"
    cur.execute(
        "SELECT c.relrowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'ops' AND c.relname = 'spend_rejections'"
    )
    assert not cur.fetchone()[0], "ops.spend_rejections: no debería tener RLS"
    # ops.auth_dances (ADR-019): sin RLS — plano proveedor pre-autenticación.
    cur.execute(
        "SELECT c.relrowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'ops' AND c.relname = 'auth_dances'"
    )
    assert not cur.fetchone()[0], "ops.auth_dances: no debería tener RLS"
    # ops.model_provider_tenants (spec 036, D-036-01): SÍ lleva tenant_id —
    # es la política de accesos DEL PROVIDER (habilitación por cliente), no
    # dato de negocio del tenant; sin RLS por diseño. El aislamiento real
    # vive en catalog.model_catalog (filas source='provider' bajo RLS,
    # AC-036-07): sin fila de habilitación el cliente no ve nada.
    cur.execute(
        "SELECT c.relrowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'ops' AND c.relname = 'model_provider_tenants'"
    )
    assert not cur.fetchone()[0], \
        "ops.model_provider_tenants: no debería tener RLS"


def test_app_role_is_neither_superuser_nor_owner_nor_bypassrls(admin_conn):
    cur = admin_conn.cursor()
    cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'trust_app'")
    super_, bypass = cur.fetchone()
    assert not super_ and not bypass
    cur.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE r.rolname = 'trust_app' AND n.nspname IN ('public','catalog','ops')"
    )
    assert cur.fetchone()[0] == 0, "trust_app no debe ser dueño de ningún objeto"
    cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'trust_migrator'")
    assert cur.fetchone()[0] is True, "trust_migrator debe tener BYPASSRLS (ops auditadas)"


def test_grants_match_spec(admin_conn):
    cur = admin_conn.cursor()
    cases = [
        # (tabla, privilegio, esperado) — AC-DB-002
        ("audit_log", "update", False),
        ("audit_log", "delete", False),
        ("audit_log", "insert", True),
        ("audit_log", "select", True),
        ("spend_ledger", "insert", True),
        ("jobs", "delete", True),
        ("catalog.policy_keys", "insert", False),
        ("catalog.policy_keys", "select", True),
        # Spec 032: el discovery escribe filas byok como trust_app (migración
        # 0024 §19.3); el resto del catálogo sigue siendo solo-lectura.
        ("catalog.model_catalog", "select", True),
        ("catalog.model_catalog", "insert", True),
        ("catalog.model_catalog", "update", True),
        ("catalog.model_catalog", "delete", True),
        ("catalog.connector_catalog", "update", False),
        ("ops.spend_cursor", "update", True),
        ("ops.spend_cursor", "delete", False),
        ("ops.login_throttle", "insert", True),
        ("ops.login_throttle", "update", True),
        ("ops.login_throttle", "delete", False),
        # ops.spend_rejections (QA H2, D-DB-15): evidencia append-only.
        ("ops.spend_rejections", "insert", True),
        ("ops.spend_rejections", "select", True),
        ("ops.spend_rejections", "update", False),
        ("ops.spend_rejections", "delete", False),
    ]
    for table, priv, expected in cases:
        cur.execute("SELECT has_table_privilege('trust_app', %s, %s)", (table, priv))
        got = cur.fetchone()[0]
        assert got is expected, f"trust_app {priv} sobre {table}: esperado {expected}, obtuve {got}"
    # Pitfall #1 del skill: grants de secuencias
    cur.execute("SELECT has_sequence_privilege('trust_app', 'audit_log_id_seq', 'usage')")
    assert cur.fetchone()[0] is True
    cur.execute(
        "SELECT has_sequence_privilege('trust_app', 'ops.spend_rejections_id_seq', 'usage')"
    )
    assert cur.fetchone()[0] is True


def test_default_deny_without_tenant_context(app_conn):
    """AC-DB-031: sin app.tenant_id la app ve 0 filas en TODO (fail-closed)."""
    cur = app_conn.cursor()
    for table in BUSINESS_TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        assert cur.fetchone()[0] == 0, f"{table}: default deny violado"


def test_tenant_first_index_on_every_business_table(admin_conn):
    """AC-DB-033: cada tabla de negocio tiene un índice con tenant_id primero."""
    cur = admin_conn.cursor()
    for table in BUSINESS_TABLES:
        cur.execute(
            """
            SELECT count(*) FROM pg_index x
            JOIN pg_class t ON t.oid = x.indrelid
            JOIN pg_class i ON i.oid = x.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = x.indkey[0]
            WHERE n.nspname = 'public' AND t.relname = %s AND a.attname = 'tenant_id'
            """,
            (table,),
        )
        assert cur.fetchone()[0] >= 1, f"{table}: sin índice (tenant_id, ...)"


def test_catalog_seeds_present(admin_conn):
    """AC-DB-003: catálogos como datos (matriz RBAC, policy keys, conectores...)."""
    cur = admin_conn.cursor()
    cur.execute("SELECT count(*) FROM catalog.roles")
    assert cur.fetchone()[0] == 4
    cur.execute("SELECT count(*) FROM catalog.permissions")
    assert cur.fetchone()[0] >= 17
    cur.execute("SELECT count(*) FROM catalog.role_permissions")
    assert cur.fetchone()[0] >= 34
    cur.execute("SELECT count(*) FROM catalog.policy_keys WHERE invariable")
    # require_mfa pasó a org-tunable (migración 0014, spec 015 D-AUTH-07).
    assert cur.fetchone()[0] == 3
    cur.execute("SELECT count(*) FROM catalog.policy_keys")
    # 12 originales + 4 del governance bundle (migración 0011, lane 017:
    # allowed_tools, offline_grace_minutes, unbind_mode, attestation_overdue_hours)
    # + 1 de conexiones personalizadas (migración 0012, spec 024:
    # allowed_connections) + 1 de auto-registro (migración 0014, spec 015:
    # allow_self_registration) + 29 del catálogo tipado v2 (migración 0022,
    # spec 029, t_fb62560f: matriz de capacidades P-1..P-29) = 47.
    assert cur.fetchone()[0] == 47
    cur.execute("SELECT count(*) FROM catalog.connector_catalog")
    assert cur.fetchone()[0] == 6
    cur.execute(
        "SELECT array_agg(slug ORDER BY slug) FROM catalog.connector_catalog"
    )
    assert cur.fetchone()[0] == ["email", "slack", "teams", "telegram", "vmware", "whatsapp"]
    cur.execute("SELECT count(*) FROM catalog.model_catalog")
    assert cur.fetchone()[0] >= 2
    cur.execute("SELECT count(*) FROM catalog.audit_actions")
    assert cur.fetchone()[0] >= 25


def test_rbac_matrix_matches_spec(admin_conn):
    """La matriz sembrada coincide 1:1 con spec 001 §2.1."""
    cur = admin_conn.cursor()
    cur.execute(
        "SELECT r.key, array_agg(p.permission_key ORDER BY p.permission_key) "
        "FROM catalog.roles r JOIN catalog.role_permissions p ON p.role_key = r.key "
        "GROUP BY r.key ORDER BY r.key"
    )
    matrix = dict(cur.fetchall())
    # platform_admin: la base de spec 001 §2.1 + extensiones data-driven —
    # tenants.delete (migración 0031, tenant deprovision) y providers.manage
    # (migración 0032, spec 036: registro de model providers + habilitación
    # por cliente, D-036-04).
    assert set(matrix["platform_admin"]) == {
        "tenants.provision", "catalog.manage",
        "tenants.delete", "providers.manage",
    }
    assert "budget.org.manage" in matrix["org_admin"]
    assert "policies.org.manage" in matrix["org_admin"]
    assert "policies.org.manage" not in matrix["dept_admin"]
    assert "desktop.use" in matrix["user"]
    assert "users.invite" not in matrix["user"]
