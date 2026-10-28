"""Post-provisioning healthcheck (spec 004, REQ-ONB-032, D-ONB-11).

Read-only verification of a freshly provisioned tenant: database state
(as trust_app inside the tenant scope), identity provider reachability
(discovery + JWKS over real HTTP), the initial admin user in the realm, and
the audit chain integrity. Runs AFTER the commit — a failure is reported,
never rolled back (audit_log is append-only; there is no post-commit
deletion path).
"""
import httpx
from sqlalchemy import text

from ..db import set_tenant
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .provision import find_tenant_by_slug


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "status": "ok" if ok else "failed", "detail": detail}


def healthcheck(
    engine,
    migrator_engine,
    kc: KeycloakAdminClient,
    slug: str,
) -> dict:
    checks: list[dict] = []

    tenant = find_tenant_by_slug(migrator_engine, slug)
    if tenant is None:
        return {
            "status": "failed",
            "checks": [_check("tenant_row", False, f"no tenant with slug '{slug}'")],
        }
    tenant_id = str(tenant["id"])
    issuer = kc.issuer_for(slug)

    # --- Database checks (trust_app, tenant scope) ---------------------------
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        trow = conn.execute(
            text("SELECT id, status, oidc_issuer_url, branding FROM tenants")
        ).mappings().first()
        checks.append(_check(
            "tenant_row", trow is not None and trow["status"] == "active",
            f"status={trow['status'] if trow else 'missing'}",
        ))

        drow = conn.execute(
            text("SELECT count(*) AS n FROM departments")
        ).mappings().first()
        checks.append(_check(
            "departments", drow["n"] > 0, f"{drow['n']} seeded",
        ))

        brow = conn.execute(
            text("SELECT count(*) AS n FROM budgets WHERE level = 'org' "
                 "AND period = 'monthly'")
        ).mappings().first()
        checks.append(_check(
            "org_budget", brow["n"] == 1, f"{brow['n']} org monthly budget(s)",
        ))

        # Every invariable policy key must be active at org level with the
        # catalog default value (data-driven expectation, D-ONB-07).
        prows = conn.execute(
            text(
                "SELECT k.key, k.default_value, "
                " (SELECT p.value FROM policies p WHERE p.tenant_id = "
                "   CAST(:tid AS uuid) AND p.level = 'org' AND p.key = k.key "
                "   AND p.is_active ORDER BY p.version DESC LIMIT 1) AS value "
                "FROM catalog.policy_keys k WHERE k.invariable ORDER BY k.key"
            ),
            {"tid": tenant_id},
        ).mappings()
        missing = [r["key"] for r in prows if r["value"] is None]
        checks.append(_check(
            "default_policies", not missing,
            "all invariable policies active" if not missing
            else f"missing: {', '.join(missing)}",
        ))

        mrow = conn.execute(
            text("SELECT count(*) AS n FROM model_access "
                 "WHERE status = 'approved' AND level = 'org'")
        ).mappings().first()
        checks.append(_check(
            "model_access", mrow["n"] > 0, f"{mrow['n']} approved org model(s)",
        ))

        arow = conn.execute(
            text("SELECT count(*) AS n FROM audit_log "
                 "WHERE action = 'tenant.provisioned'")
        ).mappings().first()
        checks.append(_check(
            "audit_provisioned", arow["n"] == 1,
            f"{arow['n']} tenant.provisioned event(s)",
        ))

        from ..audit.service import verify_chain

        chain = verify_chain(conn, tenant_id)
        checks.append(_check(
            "audit_chain", chain["status"] == "ok", f"chain {chain['status']}",
        ))

        admin_email_row = conn.execute(
            text("SELECT email FROM users ORDER BY created_at LIMIT 1")
        ).mappings().first()
        admin_email = admin_email_row["email"] if admin_email_row else None

    # --- Identity provider checks (real HTTP) ---------------------------------
    try:
        resp = httpx.get(
            f"{issuer}/.well-known/openid-configuration", timeout=10
        )
        checks.append(_check(
            "idp_discovery", resp.status_code == 200,
            f"HTTP {resp.status_code}",
        ))
    except httpx.HTTPError as exc:
        checks.append(_check("idp_discovery", False, str(exc)[:120]))
    try:
        resp = httpx.get(f"{issuer}/protocol/openid-connect/certs", timeout=10)
        ok = resp.status_code == 200 and "keys" in resp.json()
        checks.append(_check("idp_jwks", ok, f"HTTP {resp.status_code}"))
    except httpx.HTTPError as exc:
        checks.append(_check("idp_jwks", False, str(exc)[:120]))

    try:
        user = kc.find_user(slug, admin_email) if admin_email else None
        ok = user is not None and user.get("enabled") is True
        detail = (
            f"user={user.get('username')} enabled={user.get('enabled')} "
            f"requiredActions={user.get('requiredActions')}"
            if user else "admin user not found in realm"
        )
        checks.append(_check("idp_admin_user", ok, detail))
    except KeycloakAdminError as exc:
        checks.append(_check("idp_admin_user", False, exc.detail[:120]))

    # MFA evidence wiring (D-MFA-01 + spec 015 D-AUTH-06): without it no real
    # KC26 login can ever produce acr evidence (Keycloak never emits `amr`).
    mfa_state = kc.mfa_acr_state(slug)
    checks.append(_check(
        "idp_mfa_acr",
        mfa_state["acr_loa_map"] and mfa_state["loa_flow_ready"],
        f"acr.loa.map={mfa_state['acr_loa_map']} "
        f"browserFlow={mfa_state['browser_flow']} "
        f"loa_flow_ready={mfa_state['loa_flow_ready']}",
    ))

    status = "ok" if all(c["status"] == "ok" for c in checks) else "failed"
    return {"status": status, "checks": checks}
