#!/usr/bin/env python
"""Sweeper de tenants de prueba (t_4f4b0eb1, repro Miller).

CONVENCIÓN ANTI-BASURA de la fábrica: TODO tenant creado por e2e/tests/
evidence/QA de la fábrica lleva prefijo ``e2e-`` (o ``test-``) y se limpia
al terminar. Este sweeper es la red de seguridad: borra de la BD real
todo tenant que matchee los patrones de fábrica, vía el MISMO servicio
de deprovisioning del control plane (realm de Keycloak + todas las filas
+ audit ``tenant.deleted`` en ops.provider_audit). Nada simulado.

Dos modos:

  1. Explícito (recomendado para limpiezas puntuales):
       sweep_test_tenants.py --from-file slugs.txt [--yes]
     Una lista curada a mano: nada que no esté en el archivo se toca.

  2. Patrones (higiene continua):
       sweep_test_tenants.py --patterns [--keep arce-demo ...] [--yes]
     Borra todo slug que matchee TEST_PATTERNS menos los --keep.

``--dry-run`` (default) solo lista lo que borraría. ``--yes`` ejecuta.

Requisitos: mismo entorno que el API (TRUST_DATABASE_URL,
TRUST_DATABASE_MIGRATOR_URL, TRUST_KEYCLOAK_*). Dentro del contenedor:

    docker cp platform/scripts/sweep_test_tenants.py trust-agent-api:/tmp/
    docker exec trust-agent-api python /tmp/sweep_test_tenants.py --dry-run
"""
import argparse
import json
import re
import sys

# Patrones de fábrica (convención + basura histórica 2026-10). El prefijo
# canónico GOING FORWARD es ^e2e- ; los demás cubren los residuos creados
# antes de la convención (dev-f1-*, pilar2-*, qa-*, b3-*, ...).
TEST_PATTERNS = [
    r"^e2e-",            # convención canónica de la fábrica
    r"^test",            # test / test2 / test-*
    r"^qa-",
    r"^dev-",
    r"^probe",
    r"^pilar2-",
    r"^p2r\d*",
    r"^b3-",
    r"^m1-",
    r"^f0d3-",
    r"^t42e2-",
    r"^catalog-sync-",
    r"^dyn-disc",
    r"^litellm-real",
    r"^selfsignup-e2e-",
    r"^panel-e2e-",
    r"^gate-",
    r"^final-e2e$",
    r"^console-smoke",
    r"^hermes-dl-e2e$",
    r"^cli-gov-e2e$",
    r"^model-access-maas-",
    r"^maas\d+",
    r"^authux",
    r"^profiles-e2e-",
    r"^spec020",
    r"^screenshot-org-",
    r"^acme-browser-",
    r"^uuid-fix$",
    r"^custom-conn$",
    r"^dept-crud-ux$",
    r"^audit-ux$",
    r"^f1-verify$",
    r"^miller-selfsignup-",  # repros de fábrica del flujo self-signup
    r"^miller-dept-",        # repros de fábrica del flujo departments
    r"^millrepro$",          # repro de fábrica (modelos)
    r"^policies-ux",         # panel UX de políticas
]
_COMPILED = [re.compile(p) for p in TEST_PATTERNS]


def _is_test_slug(slug: str) -> bool:
    return any(p.match(slug) for p in _COMPILED)


def _list_slugs(migrator_engine):
    from sqlalchemy import text

    with migrator_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT slug, name, status FROM tenants ORDER BY slug")
        ).mappings().all()
    return [dict(r) for r in rows]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="sweep-test-tenants",
        description="Bulk-deprovision factory test tenants (real service).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-file", help="File with one slug per line.")
    mode.add_argument(
        "--patterns", action="store_true",
        help=f"Sweep every slug matching the {len(TEST_PATTERNS)} factory "
             "test patterns (minus --keep).",
    )
    parser.add_argument(
        "--keep", nargs="*", default=[],
        help="Slugs to NEVER touch (real customers).",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Actually delete (default: dry-run, only lists).",
    )
    args = parser.parse_args(argv)

    from app.config import get_settings
    from app.db import get_migrator_engine
    from app.tenants.deprovision import deprovision
    from app.tenants.keycloak_admin import KeycloakAdminClient

    keep = set(args.keep)
    tenants = _list_slugs(get_migrator_engine())

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            wanted = {line.strip() for line in fh if line.strip()}
        targets = [t for t in tenants if t["slug"] in wanted]
        missing = wanted - {t["slug"] for t in targets}
        if missing:
            print(f"NOTE: {len(missing)} slug(s) not found (already gone): "
                  + ", ".join(sorted(missing)))
    else:
        targets = [t for t in tenants if _is_test_slug(t["slug"])]

    targets = [t for t in targets if t["slug"] not in keep]
    if not targets:
        print("Nothing to sweep.")
        return 0

    print(f"{'WOULD DELETE' if not args.yes else 'DELETING'} "
          f"{len(targets)} tenant(s):")
    for t in targets:
        print(f"  - {t['slug']}  ({t['name']}, {t['status']})")
    if not args.yes:
        print("\nDry-run only. Re-run with --yes to deprovision them.")
        return 0

    kc = KeycloakAdminClient(get_settings())
    failures = []
    for t in targets:
        try:
            result = deprovision(
                get_migrator_engine(), kc, t["slug"], t["slug"],
                operator={"id": None, "username": None, "via": "sweeper"},
            )
            flag = "" if result["realm_deleted"] else "  [REALM ORPHAN!]"
            print(f"  deleted {t['slug']}: "
                  f"{sum(result['removed'].values())} rows"
                  f"{', realm gone' if result['realm_deleted'] else ', REALM FAILED'}"
                  f"{flag}")
            if result["warning"]:
                failures.append(result["warning"])
        except Exception as exc:  # noqa: BLE001 — report and continue
            failures.append(f"{t['slug']}: {exc}")
            print(f"  FAILED {t['slug']}: {exc}")

    remaining = _list_slugs(get_migrator_engine())
    print(f"\nRemaining tenants ({len(remaining)}):")
    for t in remaining:
        print(f"  = {t['slug']}  ({t['name']}, {t['status']})")
    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        print(json.dumps(failures, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
