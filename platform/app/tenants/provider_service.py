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
from ..errors import conflict, not_found, validation_failed
from ..governance.budgets import _cents_out
from .provision import find_tenant_by_slug, tenant_state

# Acciones de audit por estado destino (D-PC-05).
_STATUS_ACTIONS = {"suspended": "tenant.suspended", "active": "tenant.reactivated"}


def list_tenants(
    engine: Engine, migrator_engine: Engine, status: str | None = None
) -> dict:
    """Lista de tenants con stats por tenant (REQ-PC-001, D-PC-04/08).

    Las filas base se leen vía migrator (cross-tenant, solo el proveedor);
    los stats de cada tenant se leen como trust_app DENTRO de su scope.
    ``status`` (spec 026) filtra en SQL — p.ej. pending_activation para la
    cola de activación del Provider Console.
    """
    query = (
        "SELECT id, slug, name, status, oidc_realm, oidc_issuer_url, "
        "created_at FROM tenants"
    )
    params: dict = {}
    if status is not None:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY created_at, slug"
    with migrator_engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()

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
        # Milli-centavo (D-GOV-17, migración 0029): spent_cents es
        # numeric(12,3); int() truncaría el sub-centavo acumulado — el mismo
        # gasto invisible del gap B-2, ahora en el panel del proveedor.
        # _cents_out: int si es entero (contrato previo), float si hay fracción.
        item["spent_cents_period"] = _cents_out(stats["spent_cents_period"])
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


def activate_tenant(
    engine: Engine, migrator_engine: Engine, slug: str,
    initial_budget_usd: float,
) -> dict:
    """Activa un tenant `pending_activation` + budget inicial (spec 026,
    REQ-SS-003, D-26-05).

    UNA transacción como trust_app en el scope del tenant:
      - status pending_activation -> active (audit `tenant.activated`),
      - upsert del budget org con el monto inicial (audit `budget.assigned`),
      - notificación `tenant_activated` a cada org_admin (canal existente).
    El proveedor sigue pudiendo suspender (set_tenant_status) y ajustar el
    budget después (spec 012) — este endpoint es SOLO la activación inicial.
    """
    if initial_budget_usd <= 0:
        raise validation_failed("initial_budget_usd must be positive.")
    amount_cents = int(round(initial_budget_usd * 100))

    existing = find_tenant_by_slug(migrator_engine, slug)
    if existing is None:
        raise not_found("Tenant not found.")
    tenant_id = str(existing["id"])

    from ..governance import notifications as inbox

    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        current = conn.execute(
            text("SELECT status FROM tenants")
        ).scalar_one_or_none()
        if current is None:
            raise not_found("Tenant not found.")
        if current == "active":
            raise conflict("Tenant is already active.")
        if current != "pending_activation":
            raise conflict(
                "Tenant is suspended — use the reactivate endpoint instead."
            )

        # 1) Status -> active.
        conn.execute(
            text("UPDATE tenants SET status = 'active'"),
        )
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="tenant.activated", target_type="tenant",
            target_id=tenant_id,
            payload={"previous": current, "new": "active",
                     "initial_budget_usd": initial_budget_usd},
        )

        # 2) Initial org budget (upsert — el signup lo deja en 0¢).
        budget = conn.execute(
            text(
                "SELECT id FROM budgets WHERE level = 'org' "
                "AND department_id IS NULL AND user_id IS NULL"
            )
        ).mappings().first()
        if budget is not None:
            conn.execute(
                text(
                    "UPDATE budgets SET amount_cents = :amount, "
                    "period = 'monthly', status = 'active', updated_at = now() "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"amount": amount_cents, "id": str(budget["id"])},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO budgets (tenant_id, level, amount_cents, "
                    "period) VALUES (CAST(:t AS uuid), 'org', :amount, "
                    "'monthly')"
                ),
                {"t": tenant_id, "amount": amount_cents},
            )
        audit_service.emit(
            conn, tenant_id=tenant_id, actor_role="provider",
            action="budget.assigned", target_type="budget", target_id="org",
            payload={"level": "org", "amount_cents": amount_cents,
                     "period": "monthly",
                     "initial_budget_usd": initial_budget_usd},
        )

        # 3) Notify every org_admin (REQ-SS-004, canal existente).
        notified = inbox.notify_role(
            conn, tenant_id=tenant_id, role_key="org_admin",
            type="tenant_activated",
            payload={
                "message": (
                    "Your organization is now active. An initial budget of "
                    f"USD {initial_budget_usd:.2f} has been assigned."
                ),
                "initial_budget_usd": initial_budget_usd,
                "activated_by": "provider",
            },
        )

    state = tenant_state(engine, tenant_id)
    return {"tenant": state, "notified_org_admins": notified}
