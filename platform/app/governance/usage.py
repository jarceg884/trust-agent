"""Usage analytics sobre el ledger real (spec 035, mandato Miller).

El admin ve los REQUESTS y CONSUMOS por usuario — no solo budget vs
spent. Toda cifra sale de `spend_ledger` (una fila por request, arq.
§5.3) vía SQL agregado; nada simulado (constitution I).

Tres lecturas comparten UNA sola definición de filtros (D-035-02):

- `aggregate`  — GET /api/usage: totales + una fila por usuario con
  sus modelos (REQ-USG-001).
- `requests_page` — GET /api/usage/requests: drill-down paginado de
  requests individuales (REQ-USG-002).
- `export_rows` + `rows_to_csv` — GET /api/usage/export (REQ-USG-003).

Scope (D-035-04): org_admin ve todo el tenant; dept_admin solo las
filas cuyo `department_id` está en su scope (el ingest del worker ya
resuelve el departamento por fila). Un request con `department_id`
NULL (usuario sin depto) solo lo ve org_admin. El aislamiento entre
tenants lo garantiza RLS: todas las queries corren en la transacción
tenant-scoped de `get_tx` (D-CP-02).

Nota de índices: la ventana filtra por `occurred_at::date BETWEEN`
(coincide con `budgets/history`), que no aprovecha el rango del
índice `(tenant_id, occurred_at)`; aceptable a esta escala (desarrollo
local primero, constitution VII) y documentado en el plan §riesgos.
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..errors import validation_failed
from .budgets import _cents_out, today

# Tope defensivo de filas por export (mismo espíritu que
# audit.EXPORT_ROW_CAP): un CSV de usage no es un respaldo del ledger.
EXPORT_ROW_CAP = 50_000

_PERIODS = ("today", "7d", "30d", "custom")
_MODES = ("central", "byok")


# --- Ventana de período (REQ-USG-001) --------------------------------------------

def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise validation_failed(
            f"Invalid {name} date: {value!r} (expected YYYY-MM-DD)."
        )


def resolve_window(
    period: str, from_: str | None, to: str | None
) -> dict:
    """(kind, from, to) de la ventana inclusiva, reloj UTC (D-035-03)."""
    day = today()
    if period == "today":
        return {"kind": period, "from": day, "to": day}
    if period == "7d":
        return {"kind": period, "from": day - timedelta(days=6),
                "to": day}
    if period == "30d":
        return {"kind": period, "from": day - timedelta(days=29),
                "to": day}
    if period == "custom":
        if not from_ or not to:
            raise validation_failed(
                "period=custom requires from and to (YYYY-MM-DD)."
            )
        start, end = _parse_date(from_, "from"), _parse_date(to, "to")
        if end < start:
            raise validation_failed("to must be >= from.")
        return {"kind": "custom", "from": start, "to": end}
    raise validation_failed(
        f"Unknown period: {period!r} (expected one of {', '.join(_PERIODS)})."
    )


# --- Filtros compartidos (D-035-02) ----------------------------------------------

def validate_filters(model: str | None, mode: str | None) -> None:
    if mode is not None and mode not in _MODES:
        raise validation_failed(
            f"Unknown mode: {mode!r} (expected central or byok)."
        )
    if model is not None and not model.strip():
        raise validation_failed("model filter must be a non-empty name.")


def _where(
    window: dict, *, scope_depts: list[str] | None,
    user_id: str | None, model: str | None, mode: str | None,
) -> tuple[str, dict]:
    """Cláusula WHERE + params compartida por las tres lecturas."""
    clauses = [
        "l.occurred_at::date >= :from_d",
        "l.occurred_at::date <= :to_d",
    ]
    params: dict = {"from_d": window["from"], "to_d": window["to"]}
    if scope_depts is not None:
        # dept_admin: solo filas de sus departamentos (D-035-04).
        clauses.append("l.department_id = ANY(CAST(:depts AS uuid[]))")
        params["depts"] = scope_depts
    if user_id is not None:
        clauses.append("l.user_id = CAST(:usr AS uuid)")
        params["usr"] = user_id
    if model is not None:
        clauses.append("l.model_name = :model")
        params["model"] = model
    if mode is not None:
        clauses.append("l.mode = :mode")
        params["mode"] = mode
    return " AND ".join(clauses), params


def _user_join(where: str) -> str:
    # LEFT JOIN: el ledger admite user_id NULL (requests sin usuario
    # resuelto); se agrupan bajo "(unassigned)" en vez de desaparecer.
    return (
        "FROM spend_ledger l "
        "LEFT JOIN users u ON u.id = l.user_id "
        "LEFT JOIN departments d ON d.id = l.department_id "
        f"WHERE {where}"
    )


# --- REQ-USG-001 — agregado por usuario ------------------------------------------

def aggregate(
    conn: Connection, *, scope_depts: list[str] | None, window: dict,
    user_id: str | None = None, model: str | None = None,
    mode: str | None = None,
) -> dict:
    validate_filters(model, mode)
    where, params = _where(
        window, scope_depts=scope_depts, user_id=user_id,
        model=model, mode=mode,
    )
    totals = conn.execute(text(
        "SELECT count(*) AS requests, "
        "COALESCE(SUM(l.tokens_in), 0) AS tokens_in, "
        "COALESCE(SUM(l.tokens_out), 0) AS tokens_out, "
        "COALESCE(SUM(l.cost_base_cents), 0) AS cost_base_cents, "
        "COALESCE(SUM(l.cost_billed_cents), 0) AS cost_billed_cents "
        f"FROM spend_ledger l WHERE {where}"
    ), params).mappings().first()

    rows = conn.execute(text(
        "SELECT l.user_id, u.email, u.display_name, "
        "l.department_id, d.name AS department_name, "
        "count(*) AS requests, "
        "COALESCE(SUM(l.tokens_in), 0) AS tokens_in, "
        "COALESCE(SUM(l.tokens_out), 0) AS tokens_out, "
        "COALESCE(SUM(l.cost_base_cents), 0) AS cost_base_cents, "
        "COALESCE(SUM(l.cost_billed_cents), 0) AS cost_billed_cents, "
        "count(*) FILTER (WHERE l.mode = 'central') AS central_requests, "
        "count(*) FILTER (WHERE l.mode = 'byok') AS byok_requests "
        + _user_join(where) +
        " GROUP BY l.user_id, u.email, u.display_name, "
        "l.department_id, d.name "
        "ORDER BY cost_billed_cents DESC, u.email NULLS LAST"
    ), params).mappings().all()

    # Desglose por modelo por usuario (misma ventana/filtros).
    model_rows = conn.execute(text(
        "SELECT l.user_id, l.model_name, l.mode, count(*) AS requests, "
        "COALESCE(SUM(l.cost_billed_cents), 0) AS cost_billed_cents "
        "FROM spend_ledger l "
        f"WHERE {where} "
        "GROUP BY l.user_id, l.model_name, l.mode "
        "ORDER BY cost_billed_cents DESC, l.model_name"
    ), params).mappings().all()
    models_by_user: dict[str, list[dict]] = {}
    for r in model_rows:
        key = str(r["user_id"]) if r["user_id"] is not None else ""
        models_by_user.setdefault(key, []).append({
            "model_name": r["model_name"],
            "mode": r["mode"],
            "requests": r["requests"],
            "cost_billed_cents": _cents_out(r["cost_billed_cents"]),
        })

    users = []
    for r in rows:
        key = str(r["user_id"]) if r["user_id"] is not None else ""
        users.append({
            "user_id": (
                str(r["user_id"]) if r["user_id"] is not None else None
            ),
            "email": r["email"] or "(unassigned)",
            "display_name": r["display_name"] or "(unassigned)",
            "department_id": (
                str(r["department_id"])
                if r["department_id"] is not None else None
            ),
            "department_name": r["department_name"],
            "requests": r["requests"],
            "tokens_in": r["tokens_in"],
            "tokens_out": r["tokens_out"],
            "cost_base_cents": _cents_out(r["cost_base_cents"]),
            "cost_billed_cents": _cents_out(r["cost_billed_cents"]),
            "central_requests": r["central_requests"],
            "byok_requests": r["byok_requests"],
            "models": models_by_user.get(key, []),
        })

    return {
        "scope": "dept" if scope_depts is not None else "org",
        "period": {
            "kind": window["kind"],
            "from": window["from"].isoformat(),
            "to": window["to"].isoformat(),
        },
        "filters": {"user_id": user_id, "model": model, "mode": mode},
        "totals": {
            "requests": totals["requests"],
            "tokens_in": totals["tokens_in"],
            "tokens_out": totals["tokens_out"],
            "cost_base_cents": _cents_out(totals["cost_base_cents"]),
            "cost_billed_cents": _cents_out(totals["cost_billed_cents"]),
        },
        "users": users,
    }


# --- REQ-USG-002 — drill-down paginado -------------------------------------------

def requests_page(
    conn: Connection, *, scope_depts: list[str] | None, window: dict,
    user_id: str | None = None, model: str | None = None,
    mode: str | None = None, limit: int = 50, offset: int = 0,
) -> dict:
    validate_filters(model, mode)
    where, params = _where(
        window, scope_depts=scope_depts, user_id=user_id,
        model=model, mode=mode,
    )
    total = conn.execute(text(
        f"SELECT count(*) FROM spend_ledger l WHERE {where}"
    ), params).scalar_one()
    rows = conn.execute(text(
        "SELECT l.request_id, l.occurred_at, l.user_id, u.email, "
        "u.display_name, l.department_id, l.model_name, l.mode, "
        "l.tokens_in, l.tokens_out, l.cost_base_cents, "
        "l.cost_billed_cents " + _user_join(where) +
        " ORDER BY l.occurred_at DESC, l.request_id "
        "LIMIT :limit OFFSET :offset"
    ), {**params, "limit": limit, "offset": offset}).mappings().all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "requests": [
            {
                "request_id": r["request_id"],
                "occurred_at": r["occurred_at"].isoformat(),
                "user_id": (
                    str(r["user_id"]) if r["user_id"] is not None else None
                ),
                "email": r["email"] or "(unassigned)",
                "display_name": r["display_name"] or "(unassigned)",
                "department_id": (
                    str(r["department_id"])
                    if r["department_id"] is not None else None
                ),
                "model_name": r["model_name"],
                "mode": r["mode"],
                "tokens_in": r["tokens_in"],
                "tokens_out": r["tokens_out"],
                "cost_base_cents": _cents_out(r["cost_base_cents"]),
                "cost_billed_cents": _cents_out(r["cost_billed_cents"]),
            }
            for r in rows
        ],
    }


# --- REQ-USG-003 — export CSV -----------------------------------------------------

def export_rows(
    conn: Connection, *, scope_depts: list[str] | None, window: dict,
    level: str = "users", user_id: str | None = None,
    model: str | None = None, mode: str | None = None,
) -> list[dict]:
    """Filas (dicts planos) del nivel pedido, cap EXPORT_ROW_CAP."""
    if level == "users":
        summary = aggregate(
            conn, scope_depts=scope_depts, window=window,
            user_id=user_id, model=model, mode=mode,
        )
        return [
            {
                "user_id": u["user_id"] or "",
                "email": u["email"],
                "display_name": u["display_name"],
                "department": u["department_name"] or "",
                "requests": u["requests"],
                "tokens_in": u["tokens_in"],
                "tokens_out": u["tokens_out"],
                "cost_base_usd": _usd(u["cost_base_cents"]),
                "cost_billed_usd": _usd(u["cost_billed_cents"]),
                "models": "; ".join(
                    f"{m['model_name']}({m['mode']}):{m['requests']}"
                    for m in u["models"]
                ),
            }
            for u in summary["users"]
        ]
    if level == "requests":
        page = requests_page(
            conn, scope_depts=scope_depts, window=window,
            user_id=user_id, model=model, mode=mode,
            limit=EXPORT_ROW_CAP, offset=0,
        )
        return [
            {
                "request_id": r["request_id"],
                "occurred_at": r["occurred_at"],
                "email": r["email"],
                "display_name": r["display_name"],
                "department_id": r["department_id"] or "",
                "model_name": r["model_name"],
                "mode": r["mode"],
                "tokens_in": r["tokens_in"],
                "tokens_out": r["tokens_out"],
                "cost_base_usd": _usd_precise(r["cost_base_cents"]),
                "cost_billed_usd": _usd_precise(r["cost_billed_cents"]),
            }
            for r in page["requests"][:EXPORT_ROW_CAP]
        ]
    raise validation_failed(
        f"Unknown export level: {level!r} (expected users or requests)."
    )


def _usd(cents: Decimal | int | float) -> str:
    """Centavos → USD con 2 decimales, punto decimal, sin símbolo.

    Acepta Decimal (milli-centavo, D-GOV-17): la suma agregada de muchas
    llamadas sub-centavo produce centavos completos visibles.
    """
    return f"{cents / 100:.2f}"


def _usd_precise(cents: Decimal | int | float) -> str:
    """USD con 5 decimales para filas POR REQUEST (D-GOV-17): una llamada
    real chica de MaaS cuesta fracciones de centavo ($0.000045) y con 2
    decimales se vería $0.00 — el mismo gasto invisible del gap B-2."""
    return f"{cents / 100:.5f}"


def rows_to_csv(rows: list[dict]) -> str:
    """CSV RFC-4180 simple: cabecera = claves de la primera fila."""
    if not rows:
        return ""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()),
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
