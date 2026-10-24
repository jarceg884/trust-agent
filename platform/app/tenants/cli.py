"""trust-provision — provider CLI for tenant onboarding (spec 004, D-ONB-01).

The CLI runs on the provider's machine against the same environment as the
API (TRUST_* variables) and calls the SAME service layer as
POST /api/admin/tenants. Idiom-proof runbook: one command, exit 0 (REQ-ONB-008).

    trust-provision provision --org "Acme" --admin-email admin@acme.com \
        --budget-usd 500 [--model-plan standard|a,b] [--slug acme] \
        [--branding '{"product_name": "Acme AI"}'] [--departments it_operations]
    trust-provision verify <slug>

Output: JSON on stdout (one_time_activation ONLY on creation — never stored,
never re-displayed). Errors: envelope JSON on stderr, exit 1.
"""
import argparse
import json
import sys

from ..config import get_settings
from ..db import get_engine, get_migrator_engine
from ..errors import ApiError
from .healthcheck import healthcheck
from .keycloak_admin import KeycloakAdminClient
from .provision import ProvisionRequest, provision


def _parse_model_plan(value: str) -> str | list[str]:
    if value.strip().lower() == "standard":
        return "standard"
    return [name.strip() for name in value.split(",") if name.strip()]


def _cmd_provision(args: argparse.Namespace) -> int:
    branding = json.loads(args.branding) if args.branding else None
    departments = (
        [d.strip() for d in args.departments.split(",") if d.strip()]
        if args.departments else None
    )
    result = provision(
        get_engine(),
        get_migrator_engine(),
        KeycloakAdminClient(get_settings()),
        ProvisionRequest(
            organization_name=args.org,
            admin_email=args.admin_email,
            initial_budget_usd=args.budget_usd,
            model_plan=_parse_model_plan(args.model_plan),
            branding=branding,
            departments=departments,
            slug=args.slug,
            admin_display_name=args.admin_display_name,
        ),
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    result = healthcheck(
        get_engine(),
        get_migrator_engine(),
        KeycloakAdminClient(get_settings()),
        args.slug,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trust-provision",
        description="Trust Agent provider CLI: tenant provisioning (spec 004).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("provision", help="Provision a complete new tenant.")
    p.add_argument("--org", required=True, help="Organization name.")
    p.add_argument("--admin-email", required=True,
                   help="Email of the initial org admin.")
    p.add_argument("--budget-usd", required=True, type=float,
                   help="Initial monthly organization budget in USD.")
    p.add_argument("--model-plan", default="standard",
                   help="'standard' or comma-separated model names.")
    p.add_argument("--slug", default=None,
                   help="Explicit tenant slug (idempotency key).")
    p.add_argument("--branding", default=None,
                   help="Branding overrides as a JSON object.")
    p.add_argument("--departments", default=None,
                   help="Comma-separated profile template keys "
                        "(default: all four).")
    p.add_argument("--admin-display-name", default=None)
    p.set_defaults(func=_cmd_provision)

    v = sub.add_parser("verify", help="Run the tenant healthcheck.")
    v.add_argument("slug", help="Tenant slug.")
    v.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ApiError as exc:
        envelope = {
            "error": {
                "code": exc.code, "reason": exc.reason,
                "message": exc.message, "request_id": None,
            }
        }
        print(json.dumps(envelope, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(json.dumps({"error": {"code": "internal_error",
                                    "reason": "internal_error",
                                    "message": str(exc), "request_id": None}},
                         indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
