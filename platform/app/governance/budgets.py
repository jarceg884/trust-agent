"""Hierarchical budgets org→dept→user (REQ-BUD-002/003/004/013, spec 005).

All amounts are integer USD cents. Sum validation: Σ dept budgets ≤ org
budget; Σ user budgets of a dept ≤ dept budget. Lowering a budget below
the spend already incurred in the current window requires force=true and
is audited (budget.forced_below_spend).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..audit import service as audit
from ..auth.service import Principal
from ..errors import not_found, validation_failed


# --- Period windows (D-GOV-07) ------------------------------------------------

def today() -> date:
    """Current date as the SINGLE clock seam for budget windows.

    budgets.py and spend.py always resolve the current day through this
    function so the rollover test (AC-BUD-010, spec 013 D-013-01) can
    simulate the clock. UTC by design: spend events carry UTC timestamps
    (`spend_ledger.occurred_at`), so the ingestion window (event date)
    and the enforcement window (today) MUST come from the same clock —
    a local-date seam would diverge from UTC counters every evening.
    """
    return datetime.now(timezone.utc).date()


def period_window(period: str, day: date) -> tuple[date, date]:
    """(period_start, period_end) of the window containing `day`."""
    if period == "daily":
        return day, day
    if period == "weekly":
        start = day - timedelta(days=day.weekday())  # Monday
        return start, start + timedelta(days=6)
    if period == "monthly":
        start = day.replace(day=1)
        if day.month == 12:
            end = date(day.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(day.year, day.month + 1, 1) - timedelta(days=1)
        return start, end
    raise validation_failed(f"Unknown budget period: {period}")


def _budget_row(
    conn: Connection, *, level: str,
    department_id: str | None = None,
    user_id: str | None = None,
) -> dict | None:
    row = conn.execute(
        text(
            "SELECT id, amount_cents, period, soft_threshold_pct, status "
            "FROM budgets WHERE level = :level AND status = 'active' "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid))"
        ),
        {
            "level": level, "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings().first()
    return dict(row) if row else None


def _spent_in_window(
    conn: Connection, *, level: str, entity_id: str,
    period_start: date, period_end: date,
) -> int:
    row = conn.execute(
        text(
            "SELECT spent_cents FROM budget_counters "
            "WHERE level = :level AND entity_id = CAST(:e AS uuid) "
            "AND period_start = :ps AND period_end = :pe"
        ),
        {"level": level, "e": entity_id, "ps": period_start, "pe": period_end},
    ).mappings().first()
    return row["spent_cents"] if row else 0


def _upsert(
    conn: Connection, *, level: str,
    department_id: str | None, user_id: str | None,
    amount_cents: int, period: str, soft_threshold_pct: int,
) -> dict:
    existing = conn.execute(
        text(
            "SELECT id FROM budgets WHERE level = :level "
            "AND (department_id IS NULL) = (:dept_null) "
            "AND (user_id IS NULL) = (:user_null) "
            "AND (CAST(:dept AS uuid) IS NULL OR department_id = CAST(:dept AS uuid)) "
            "AND (CAST(:usr AS uuid) IS NULL OR user_id = CAST(:usr AS uuid))"
        ),
        {
            "level": level, "dept": department_id, "usr": user_id,
            "dept_null": department_id is None, "user_null": user_id is None,
        },
    ).mappings().first()
    if existing is not None:
        conn.execute(
            text(
                "UPDATE budgets SET amount_cents = :amount, period = :period, "
                "soft_threshold_pct = :pct, status = 'active', updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"amount": amount_cents, "period": period, "pct": soft_threshold_pct,
             "id": str(existing["id"])},
        )
        return {"id": str(existing["id"]), "created": False}
    inserted = conn.execute(
        text(
            "INSERT INTO budgets (tenant_id, level, department_id, user_id, "
            "amount_cents, period, soft_threshold_pct) "
            "VALUES (CAST(:t AS uuid), :level, CAST(:dept AS uuid), "
            "CAST(:usr AS uuid), :amount, :period, :pct) RETURNING id"
        ),
        {
            "t": _tenant_id(conn), "level": level, "dept": department_id,
            "usr": user_id, "amount": amount_cents, "period": period,
            "pct": soft_threshold_pct,
        },
    ).mappings().first()
    return {"id": str(inserted["id"]), "created": True}


def _tenant_id(conn: Connection) -> str:
    row = conn.execute(text("SELECT id FROM tenants")).mappings().first()
    return str(row["id"])


def _actor_role(principal: Principal) -> str:
    return principal.role_keys[0] if principal.role_keys else "user"


def _audit_update(conn: Connection, principal: Principal, level: str,
                  department_id: str | None, user_id: str | None,
                  amount_cents: int, period: str) -> None:
    audit.emit(
        conn, tenant_id=principal.tenant_id, actor_role=_actor_role(principal),
        action="budget.updated", actor_id=principal.user_id,
        target_type="budget", target_id=level,
        payload={"level": level, "department_id": department_id,
                 "user_id": user_id, "amount_cents": amount_cents,
                 "period": period},
    )


def _check_force_below_spend(
    conn: Connection, principal: Principal, *, level: str,
    department_id: str | None, user_id: str | None,
    amount_cents: int, period: str, force: bool,
) -> None:
    """REQ-BUD-013: lowering below current-window spend needs force=true."""
    entity = user_id or department_id or _tenant_id(conn)
    start, end = period_window(period, today())
    spent = _spent_in_window(
        conn, level=level, entity_id=entity,
        period_start=start, period_end=end,
    )
    if amount_cents < spent and not force:
        raise validation_failed(
            f"Budget ({amount_cents} cents) is below the spend already "
            f"incurred this period ({spent} cents). Pass force=true to "
            "confirm (the decision is audited)."
        )
    if amount_cents < spent and force:
        audit.emit(
            conn, tenant_id=principal.tenant_id,
            actor_role=_actor_role(principal),
            action="budget.forced_below_spend", actor_id=principal.user_id,
            target_type="budget", target_id=level,
            payload={"level": level, "department_id": department_id,
                     "user_id": user_id, "amount_cents": amount_cents,
                     "spent_cents": spent},
        )


# --- Public API ----------------------------------------------------------------

def set_org_budget(
    conn: Connection, principal: Principal, *,
    amount_cents: int, period: str = "monthly",
    soft_threshold_pct: int = 80, force: bool = False,
) -> dict:
    _check_force_below_spend(
        conn, principal, level="org", department_id=None, user_id=None,
        amount_cents=amount_cents, period=period, force=force,
    )
    result = _upsert(
        conn, level="org", department_id=None, user_id=None,
        amount_cents=amount_cents, period=period,
        soft_threshold_pct=soft_threshold_pct,
    )
    _audit_update(conn, principal, "org", None, None, amount_cents, period)
    result.update({"level": "org", "amount_cents": amount_cents,
                   "period": period})
    return result


def set_dept_budget(
    conn: Connection, principal: Principal, *,
    department_id: str, amount_cents: int, period: str = "monthly",
    soft_threshold_pct: int = 80, force: bool = False,
) -> dict:
    dept = conn.execute(
        text("SELECT id FROM departments WHERE id = CAST(:d AS uuid)"),
        {"d": department_id},
    ).mappings().first()
    if dept is None:
        raise not_found("Department not found.")
    org = _budget_row(conn, level="org")
    if org is None:
        raise validation_failed(
            "The organization has no budget yet; set it first (REQ-BUD-001)."
        )
    # REQ-BUD-002: Σ dept budgets (with the new value) ≤ org budget.
    old = _budget_row(conn, level="dept", department_id=department_id)
    old_amount = old["amount_cents"] if old else 0
    total = conn.execute(
        text("SELECT COALESCE(SUM(amount_cents), 0) AS s FROM budgets "
             "WHERE level = 'dept' AND status = 'active'")
    ).mappings().first()["s"]
    if total - old_amount + amount_cents > org["amount_cents"]:
        excess = total - old_amount + amount_cents - org["amount_cents"]
        raise validation_failed(
            f"Department budgets would exceed the organization budget by "
            f"{excess} cents (Σ departments = {total - old_amount + amount_cents}"
            f" > org = {org['amount_cents']})."
        )
    _check_force_below_spend(
        conn, principal, level="dept", department_id=department_id,
        user_id=None, amount_cents=amount_cents, period=period, force=force,
    )
    result = _upsert(
        conn, level="dept", department_id=department_id, user_id=None,
        amount_cents=amount_cents, period=period,
        soft_threshold_pct=soft_threshold_pct,
    )
    _audit_update(conn, principal, "dept", department_id, None,
                  amount_cents, period)
    result.update({"level": "dept", "department_id": department_id,
                   "amount_cents": amount_cents, "period": period})
    return result


def set_user_budget(
    conn: Connection, principal: Principal, *,
    user_id: str, amount_cents: int, period: str = "monthly",
    soft_threshold_pct: int = 80, force: bool = False,
) -> dict:
    user = conn.execute(
        text("SELECT id, department_id FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": user_id},
    ).mappings().first()
    if user is None:
        raise not_found("User not found.")
    if not user["department_id"]:
        raise validation_failed("The user has no department; user budgets "
                                "are scoped to a department.")
    dept_id = str(user["department_id"])
    dept = _budget_row(conn, level="dept", department_id=dept_id)
    if dept is None:
        raise validation_failed(
            "The user's department has no budget yet; set it first."
        )
    # REQ-BUD-003: Σ user budgets of the dept (with the new value) ≤ dept.
    old = _budget_row(conn, level="user", department_id=dept_id, user_id=user_id)
    old_amount = old["amount_cents"] if old else 0
    total = conn.execute(
        text(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM budgets "
            "WHERE level = 'user' AND status = 'active' "
            "AND department_id = CAST(:d AS uuid)"
        ),
        {"d": dept_id},
    ).mappings().first()["s"]
    if total - old_amount + amount_cents > dept["amount_cents"]:
        excess = total - old_amount + amount_cents - dept["amount_cents"]
        raise validation_failed(
            f"User budgets of the department would exceed the department "
            f"budget by {excess} cents."
        )
    _check_force_below_spend(
        conn, principal, level="user", department_id=dept_id,
        user_id=user_id, amount_cents=amount_cents, period=period,
        force=force,
    )
    result = _upsert(
        conn, level="user", department_id=dept_id, user_id=user_id,
        amount_cents=amount_cents, period=period,
        soft_threshold_pct=soft_threshold_pct,
    )
    _audit_update(conn, principal, "user", dept_id, user_id,
                  amount_cents, period)
    result.update({"level": "user", "department_id": dept_id,
                   "user_id": user_id, "amount_cents": amount_cents,
                   "period": period})
    return result


@dataclass
class LevelStatus:
    level: str
    budget_cents: int | None
    spent_cents: int
    period: str
    cutoff: bool

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "budget_cents": self.budget_cents,
            "spent_cents": self.spent_cents,
            "remaining_cents": (
                self.budget_cents - self.spent_cents
                if self.budget_cents is not None else None
            ),
            "period": self.period,
            "cutoff": self.cutoff,
        }


def _level_status(
    conn: Connection, *, level: str, entity_id: str,
    department_id: str | None = None, user_id: str | None = None,
) -> LevelStatus:
    budget = _budget_row(
        conn, level=level, department_id=department_id, user_id=user_id
    )
    if budget is None:
        return LevelStatus(level, None, 0, "monthly", False)
    start, end = period_window(budget["period"], today())
    spent = _spent_in_window(
        conn, level=level, entity_id=entity_id,
        period_start=start, period_end=end,
    )
    return LevelStatus(
        level, budget["amount_cents"], spent, budget["period"],
        budget["amount_cents"] > 0 and spent >= budget["amount_cents"],
    )


def status_for_user(
    conn: Connection, tenant_id: str, user_id: str,
    department_id: str | None,
) -> dict:
    """GET /api/budgets/me (REQ-GOV-012)."""
    user = _level_status(
        conn, level="user", entity_id=user_id,
        department_id=department_id, user_id=user_id,
    )
    dept = (
        _level_status(conn, level="dept", entity_id=department_id,
                      department_id=department_id)
        if department_id else LevelStatus("dept", None, 0, "monthly", False)
    )
    org = _level_status(conn, level="org", entity_id=tenant_id)
    cutoff_level = next(
        (s.level for s in (user, dept, org) if s.cutoff), None
    )
    return {
        "user": user.as_dict(),
        "department": dept.as_dict(),
        "organization": org.as_dict(),
        "cutoff": cutoff_level is not None,
        "cutoff_level": cutoff_level,
    }


def overview(conn: Connection, tenant_id: str) -> dict:
    """Org overview (REQ-BUD-004): per-level budget/spent + unassigned.

    Departments WITHOUT a budget are listed too (spec 013 D-013-07,
    AC-ORG-001): budget_cents is null until PUT /api/budgets/dept/{id}.
    """
    org = _level_status(conn, level="org", entity_id=tenant_id)
    # LEFT JOIN: every department shows up, budget or not (D-013-07).
    dept_rows = conn.execute(
        text(
            "SELECT d.id AS department_id, d.name, b.amount_cents, b.period "
            "FROM departments d LEFT JOIN budgets b "
            "  ON b.department_id = d.id AND b.level = 'dept' "
            "  AND b.status = 'active' "
            "ORDER BY d.name"
        )
    ).mappings()
    departments = []
    dept_total = 0
    for r in dept_rows:
        if r["amount_cents"] is not None:
            dept_total += r["amount_cents"]
        start, end = period_window(r["period"] or "monthly", today())
        spent = (
            _spent_in_window(
                conn, level="dept", entity_id=str(r["department_id"]),
                period_start=start, period_end=end,
            )
            if r["amount_cents"] is not None else 0
        )
        members = conn.execute(
            text("SELECT count(*) AS c FROM users "
                 "WHERE department_id = CAST(:d AS uuid)"),
            {"d": str(r["department_id"])},
        ).mappings().first()["c"]
        departments.append({
            "department_id": str(r["department_id"]),
            "name": r["name"],
            "budget_cents": r["amount_cents"],
            "spent_cents": spent,
            "members": members,
        })
    return {
        "organization": org.as_dict(),
        "departments": departments,
        "dept_budgets_total_cents": dept_total,
        "unassigned_cents": (
            org.budget_cents - dept_total
            if org.budget_cents is not None else None
        ),
    }


def history(
    conn: Connection, *, scope_depts: list[str] | None = None, limit: int = 12,
) -> dict:
    """GET /api/budgets/history (REQ-BUD-012, spec 013 D-013-03).

    CLOSED windows only (period_end < today): budget vs spent per level
    (org / departments / users) plus a per-model breakdown taken from the
    ledger for the same window, so each level reconciles with the sum of
    spend_ledger rows in that window (AC-013-012).

    scope_depts: None = whole tenant (org_admin); a list = dept_admin
    scope (org block omitted; departments, users and models filtered to
    the scoped departments).
    """
    counters = conn.execute(
        text(
            "SELECT level, entity_id, period_start, period_end, "
            "spent_cents, budget_cents FROM budget_counters "
            "WHERE period_end < :today "
            "ORDER BY period_start DESC, period_end DESC"
        ),
        {"today": today()},
    ).mappings()

    dept_names = {
        str(r["id"]): r["name"]
        for r in conn.execute(text("SELECT id, name FROM departments")).mappings()
    }
    user_meta = {
        str(r["id"]): (
            r["display_name"],
            str(r["department_id"]) if r["department_id"] else None,
        )
        for r in conn.execute(
            text("SELECT id, display_name, department_id FROM users")
        ).mappings()
    }

    windows: dict[tuple[date, date], dict] = {}
    for r in counters:
        key = (r["period_start"], r["period_end"])
        w = windows.setdefault(key, {
            "period_start": r["period_start"].isoformat(),
            "period_end": r["period_end"].isoformat(),
            "organization": None,
            "departments": [],
            "users": [],
        })
        entity = str(r["entity_id"])
        if r["level"] == "org":
            if scope_depts is None:
                w["organization"] = {
                    "budget_cents": r["budget_cents"],
                    "spent_cents": r["spent_cents"],
                }
        elif r["level"] == "dept":
            if scope_depts is None or entity in scope_depts:
                w["departments"].append({
                    "department_id": entity,
                    "name": dept_names.get(entity),
                    "budget_cents": r["budget_cents"],
                    "spent_cents": r["spent_cents"],
                })
        else:  # user
            display_name, user_dept = user_meta.get(entity, (None, None))
            if scope_depts is None or user_dept in scope_depts:
                w["users"].append({
                    "user_id": entity,
                    "display_name": display_name,
                    "department_id": user_dept,
                    "budget_cents": r["budget_cents"],
                    "spent_cents": r["spent_cents"],
                })

    out = []
    for (ps, pe), w in sorted(windows.items(), key=lambda kv: kv[0],
                              reverse=True)[: max(1, int(limit))]:
        model_sql = (
            "SELECT model_name, SUM(cost_billed_cents) AS s "
            "FROM spend_ledger "
            "WHERE occurred_at::date >= :ps AND occurred_at::date <= :pe "
        )
        params: dict = {"ps": ps, "pe": pe}
        if scope_depts is not None:
            model_sql += "AND department_id = ANY(CAST(:depts AS uuid[])) "
            params["depts"] = [_uuid.UUID(d) for d in scope_depts]
        model_sql += "GROUP BY model_name ORDER BY s DESC, model_name"
        w["by_model"] = [
            {"model_name": r["model_name"], "spent_cents": r["s"]}
            for r in conn.execute(text(model_sql), params).mappings()
        ]
        out.append(w)
    return {"periods": out}
