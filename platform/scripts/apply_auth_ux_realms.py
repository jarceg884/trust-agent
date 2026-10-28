#!/usr/bin/env python3
"""Retro-fit the spec 015 Auth UX onto EXISTING tenant realms.

Provisioning (spec 004) now wires the white-label theme, the Google IdP
(fail-closed), the tenant_id hardcode mapper and the optional-MFA LoA flow
for every NEW tenant. Realms provisioned before spec 015 keep the old
surface until this script is run — it is idempotent, so it can be re-run
after every upgrade.

Applies, per realm (D-AUTH-01/02/03/06 + v1.3.0 ADR-019):
  - ensure_login_theme   → loginTheme=trust-agent + displayNameHtml brand
                           + per-tenant `en` localization overrides (TOTP
                           enrollment h1 carries tenants.branding
                           product_name — t_e68687ee)
  - ensure_google_idp    → google IdP when TRUST_GOOGLE_CLIENT_ID/SECRET set
  - ensure_tenant_id_hardcode_mapper → signed tenant_id for brokered users
  - ensure_mfa_acr       → acr.loa.map {"1":1,"2":2} + Password LoA flow
  - ensure_direct_grant_enabled → directAccessGrantsEnabled=true (web client)
  - ensure_direct_grant_flow     → trust-direct-grant-loa bound (acr:"2"
                           evidence for password+OTP direct grants)

Usage:
  platform/.venv/bin/python platform/scripts/apply_auth_ux_realms.py \
      [--db postgres://...] [--only slug1,slug2]

Without --only it covers every tenant row in the platform database. The
Keycloak admin credential comes from the environment (TRUST_KEYCLOAK_ADMIN_*
or the values of the local .env, same bootstrap the e2e suite uses — ADR-15).
"""
import argparse
import os
import sys

PLATFORM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(PLATFORM)
sys.path.insert(0, PLATFORM)

DOTENV = os.path.join(ROOT, ".env")


def load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=os.environ.get(
            "TRUST_DATABASE_MIGRATOR_URL",
            "postgresql+psycopg://trust_migrator:trustlocal"
            "@127.0.0.1:55432/trust_agent",
        ),
        help="trust_migrator URL of the platform database",
    )
    parser.add_argument(
        "--only", default=None,
        help="comma-separated tenant slugs (default: every tenant)",
    )
    args = parser.parse_args()

    load_dotenv(DOTENV)
    # The compose file feeds Keycloak with KC_ADMIN_PASSWORD; the app reads
    # TRUST_KEYCLOAK_ADMIN_PASSWORD. Honor either (same credential).
    if os.environ.get("KC_ADMIN_PASSWORD") and not os.environ.get(
        "TRUST_KEYCLOAK_ADMIN_PASSWORD"
    ):
        os.environ["TRUST_KEYCLOAK_ADMIN_PASSWORD"] = os.environ[
            "KC_ADMIN_PASSWORD"
        ]

    from sqlalchemy import create_engine, text

    from app.config import get_settings
    from app.db import set_tenant
    from app.tenants.keycloak_admin import (
        KeycloakAdminClient,
        KeycloakAdminError,
    )

    engine = create_engine(args.db)
    kc = KeycloakAdminClient(get_settings())

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, slug, status, branding FROM tenants "
                 "ORDER BY created_at")
        ).mappings().all()

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    ok, failed = 0, 0
    for row in rows:
        slug = row["slug"]
        if only is not None and slug not in only:
            continue
        tenant_id = str(row["id"])
        branding = row["branding"] or {}
        product_name = branding.get("product_name") or None
        try:
            kc.ensure_login_theme(slug, product_name=product_name)
            google = kc.ensure_google_idp(slug)
            kc.ensure_tenant_id_hardcode_mapper(slug, tenant_id)
            kc.ensure_mfa_acr(slug)
            kc.ensure_direct_grant_enabled(slug)
            kc.ensure_direct_grant_flow(slug)
            state = kc.mfa_acr_state(slug)
            dg_state = kc.direct_grant_flow_state(slug)
            print(
                f"[ok] {slug}: theme=trust-agent "
                f"product={product_name or 'Trust Agent'} google={google} "
                f"hardcode-mapper=on loa_flow_ready="
                f"{state.get('loa_flow_ready')} "
                f"direct_grant=on flow={dg_state.get('bound_flow')} "
                f"loa={dg_state.get('loa_level')}"
            )
            ok += 1
        except KeycloakAdminError as exc:
            print(f"[FAIL] {slug}: {exc.step}: {exc.detail}", file=sys.stderr)
            failed += 1

    print(f"\n{ok} realm(s) updated, {failed} failure(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
