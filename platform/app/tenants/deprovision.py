"""Tenant deprovisioning (t_4f4b0eb1, repro Miller): la inversa de provision().

Borra un tenant COMPLETO de forma transaccional y compensable:

    PostgreSQL (UNA transacción como trust_migrator, BYPASSRLS):
        todas las filas del tenant (cascade manual por tenant_id, en orden
        inverso a las FK entre tablas de negocio) + la fila de ``tenants``
        + el evento durable ``tenant.deleted`` en ``ops.provider_audit``.
    Keycloak (después del commit):
        DELETE /admin/realms/{realm} — el realm es la última pieza porque
        un realm huérfano es INERTE (sin fila en ``tenants`` no se minta
        ninguna sesión del control plane) y el re-provisioning del mismo
        slug lo cura (los ensure_* son idempotentes). El inverso (realm
        primero, DB después) dejaría un tenant FANTASMA en el Provider
        Console si la transacción de DB fallara — peor estado.

Fronteras (constitución III):
- TODO el borrado de filas corre como ``trust_migrator`` (BYPASSRLS, ALL
  PRIVILEGES en public/catalog/ops): es una operación cross-tenant
  destructiva, el plano de la app (trust_app) ni siquiera tiene DELETE
  en audit_log (fail-closed doble, sección 11.3 de schema.sql).
- ``audit_log`` es append-only: el trigger ``audit_log_reject_mutation``
  solo acepta DELETE de trust_migrator con ``app.audit_purge='on``
  (misma excepción documentada del job de retención REQ-AUD-006). El
  deprovisioning es la purga TERMINAL de ese tenant.
- El evento ``tenant.deleted`` NO puede vivir en ``audit_log``: esa tabla
  exige ``tenant_id`` + FK a ``tenants`` y se borra CON el tenant. Va a
  ``ops.provider_audit`` (plano proveedor, cadena global append-only,
  spec 025 D-PS-07) — es el único registro durable posible y además el
  actor ES el proveedor.

Fail-closed: la confirmación debe ser el slug EXACTO; cualquier otra
cosa es 422 sin tocar nada.
"""
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..errors import not_found, validation_failed
from ..provider import audit as provider_audit
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .provision import find_tenant_by_slug

# Orden de borrado: dependientes primero (FKs entre tablas de negocio —
# ver db/schema.sql §5). Un DELETE por tabla filtrado por tenant_id; el
# rowcount real de cada uno alimenta la respuesta y el payload de audit.
#
# PITFALL (encontrado en el barrido real de los 104 tenants): las filas
# BYOK del tenant viven en el catálogo GLOBAL catalog.model_catalog
# (tenant_id + connection_id NOT NULL). El FK connection_id →
# vault_connections es ON DELETE SET NULL, pero el SET NULL viola
# model_catalog_source_scope_check (byok exige connection) — borrar
# vault_connections con filas byok vivas revienta con CheckViolation.
# Por eso el catálogo del tenant se borra ANTES (y DESPUÉS de
# model_access, que referencia model_catalog).
_DELETE_ORDER: list[str] = [
    "sessions",                # → users, devices
    "api_keys",                # → users, devices
    "agent_grants",            # → agents, users
    "agents",                  # → users (owner), departments
    "connector_instances",     # → vault_credentials
    "vault_credentials",       # → users
    "notifications",           # → users
    "jobs",
    "devices",                 # → users
    "model_access",            # → catalog.model_catalog, departments, users
    "catalog.model_catalog",   # filas byok/provider del tenant (ver pitfall)
    "vault_connections",       # → users (created_by)
    "budgets",                 # → departments, users
    "budget_counters",
    "spend_ledger",            # → users, departments
    "policies",                # → departments, users
    # spec 041: las asignaciones referencian named_policies + departments/
    # users; van ANTES que named_policies (FK) y que users/departments.
    "named_policy_assignments",
    "named_policies",
    "branding_versions",
    "branding_assets",
    "user_roles",              # → users, departments
    "users",                   # → departments
    "departments",
    # append-only: la excepción de purga (app.audit_purge='on') se activa
    # al inicio de la MISMA transacción.
    "audit_log",
    "audit_chain_checkpoint",
]

# Plano ops: tenant_id SIN FK (referencia forense / estado efímero) o con
# CASCADE (model_provider_tenants — se borra explícito para que el
# rowcount entre al reporte). Se limpian por higiene para que no quede
# basura del tenant en el plano del proveedor.
_OPS_TABLES: list[str] = [
    "ops.model_provider_tenants",
    "ops.spend_rejections",
    "ops.auth_dances",
]


def deprovision(
    migrator_engine: Engine,
    kc: KeycloakAdminClient,
    slug: str,
    confirm: str,
    *,
    operator: dict | None = None,
) -> dict:
    """Borra el tenant ``slug`` por completo (irreversible).

    ``confirm`` DEBE ser igual al slug (fail-closed). ``operator`` es el
    dict stashado por ``get_provider_principal`` en ``request.state``
    ({"id", "username", "via"}) para el payload del evento de audit.
    """
    if confirm != slug:
        raise validation_failed(
            f"Confirmation mismatch: you must type the exact slug '{slug}' "
            f"to deprovision this tenant (fail-closed)."
        )
    existing = find_tenant_by_slug(migrator_engine, slug)
    if existing is None:
        raise not_found("Tenant not found.")
    tenant_id = str(existing["id"])
    realm = existing.get("oidc_realm") or slug

    removed: dict[str, int] = {}
    with migrator_engine.begin() as conn:
        # Excepción de purga del trigger append-only (tx-local): sin esto
        # el DELETE de audit_log revienta con raise_exception.
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        for table in _DELETE_ORDER + _OPS_TABLES:
            result = conn.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": tenant_id},
            )
            removed[table] = int(result.rowcount or 0)
        # La fila del tenant va al FINAL: si cualquier FK previo falla, la
        # transacción completa (incluida la fila) se revierte.
        result = conn.execute(
            text("DELETE FROM tenants WHERE id = CAST(:t AS uuid)"),
            {"t": tenant_id},
        )
        removed["tenants"] = int(result.rowcount or 0)
        if removed["tenants"] == 0:  # pragma: no cover - raza con otro delete
            raise not_found("Tenant not found.")
        # Registro durable del deprovisioning: MISMA transacción, así el
        # evento no puede perderse (append-only, cadena global).
        provider_audit.emit(
            conn,
            action="tenant.deleted",
            operator_id=(operator or {}).get("id"),
            username=(operator or {}).get("username"),
            payload={
                "slug": slug,
                "name": existing["name"],
                "status": existing["status"],
                "realm": realm,
                "via": (operator or {}).get("via", "unknown"),
                "removed": removed,
            },
        )

    # Keycloak DESPUÉS del commit (ver docstring del módulo). Un fallo
    # aquí NO deshace el borrado: el realm huérfano es inerte y se
    # reporta con una advertencia explícita (nada simulado).
    realm_deleted = True
    warning = None
    try:
        kc.delete_realm(realm)
    except KeycloakAdminError as exc:
        realm_deleted = False
        warning = (
            f"Tenant rows are deleted, but the Keycloak realm '{realm}' "
            f"could NOT be removed ({exc.detail}). The orphan realm is "
            f"inert (no control-plane sessions can be minted without the "
            f"tenant row); re-provisioning the same slug heals it. Manual "
            f"review recommended."
        )

    return {
        "deleted": True,
        "slug": slug,
        "name": existing["name"],
        "realm": realm,
        "realm_deleted": realm_deleted,
        "warning": warning,
        "removed": removed,
    }
