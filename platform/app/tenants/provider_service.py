"""Provider operations service (spec 014, REQ-PC-001…005).

Extiende la superficie admin de spec 004 con el listado cross-tenant de
tenants (D-PC-04) y el cambio de estado suspend/reactivate (D-PC-05,
REQ-TEN-006 — aplazado desde spec 004 §1 "fuera de alcance").

Fronteras de conexión (constitución III):
- filas de `tenants` (listado / lookup por slug): conexión `trust_migrator`
  (BYPASSRLS) — generalización documentada del lookup de D-ONB-10 al MISMO
  actor (el proveedor); RLS oculta `tenants` a `trust_app` sin scope;
- TODO lo demás (stats, UPDATE de estado, audit): `trust_app` CON scope del
  tenant (`set_tenant`), igual que el resto del control plane.
"""
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..audit import service as audit_service
from ..db import set_tenant
from ..errors import not_found
from .provision import find_tenant_by_slug, tenant_state

# Acciones de audit por estado destino (D-PC-05).
_STATUS_ACTIONS = {"suspended": "tenant.suspended", "active": "tenant.reactivated"}


def list_tenants(engine: Engine, migrator_engine: Engine) -> dict:
    """Lista de tenants con stats por tenant (REQ-PC-001, D-PC-04/08).

    Las filas base se leen vía migrator (cross-tenant, solo el proveedor);
    los stats de cada tenant se leen como trust_app DENTRO de su scope.
    """
    with migrator_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, slug, name, status, oidc_realm, oidc_issuer_url, "
                "created_at FROM tenants ORDER BY created_at, slug"
            )
        ).mappings().all()

    tenants = []
    for row in rows:
        tenant_id = str(row["id"])
        item = {
            "id": tenant_id,
            "slug": row["slug"],
            "name": row["name"],
            "status": row["status"],
            "oidc_realm": row["oidc_realm"],
            "oidc_issuer_url": row["oidc_issuer_url"],
            "created_at": row["created_at"].isoformat(),
        }
        # Stats como trust_app con scope del tenant (nunca cross-tenant).
        with engine.connect() as conn:
            set_tenant(conn, tenant_id)
            stats = conn.execute(
                text(
                    "SELECT "
                    " (SELECT count(*) FROM users) AS users, "
                    " (SELECT count(*) FROM departments) AS departments, "
                    " (SELECT amount_cents FROM budgets "
                    "  WHERE level = 'org' AND status = 'active' "
                    "  ORDER BY created_at DESC LIMIT 1) AS org_budget_cents, "
                    " (SELECT COALESCE(SUM(spent_cents), 0) FROM budget_counters "
                    "  WHERE level = 'org' "
                    "  AND period_start <= current_date "
                    "  AND period_end >= current_date) AS spent_cents_period"
                )
            ).mappings().first()
        item["counts"] = {
            "users": int(stats["users"]),
            "departments": int(stats["departments"]),
        }
        item["org_budget_cents"] = (
            int(stats["org_budget_cents"]) if stats["org_budget_cents"] is not None else 0
        )
        item["spent_cents_period"] = int(stats["spent_cents_period"])
        tenants.append(item)
    return {"tenants": tenants}


def set_tenant_status(
    engine: Engine, migrator_engine: Engine, slug: str, target: str
) -> dict:
    """Suspende (`suspended`) o reactiva (`active`) un tenant (REQ-PC-002/003).

    Transaccional como trust_app en el scope del tenant: UPDATE + evento de
    audit en la MISMA transacción. Idempotente: si el tenant ya está en el
    estado destino no escribe ni audita (200 con el estado actual).
    """
    if target not in _STATUS_ACTIONS:
        raise not_found("Unsupported status.")  # unreachable vía router
    existing = find_tenant_by_slug(migrator_engine, slug)
    if existing is None:
        raise not_found("Tenant not found.")
    tenant_id = str(existing["id"])

    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        current = conn.execute(
            text("SELECT status FROM tenants")
        ).scalar_one_or_none()
        if current is None:
            raise not_found("Tenant not found.")
        if current != target:
            conn.execute(
                text("UPDATE tenants SET status = :status"),
                {"status": target},
            )
            audit_service.emit(
                conn,
                tenant_id=tenant_id,
                actor_role="provider",
                action=_STATUS_ACTIONS[target],
                target_type="tenant",
                target_id=tenant_id,
                payload={"previous": current, "new": target},
            )
    return {"tenant": tenant_state(engine, tenant_id)}
