#!/usr/bin/env python3
"""Debug de la jerarquía del flujo reestructurado (T2).

El listing de executions es PLANO y no muestra el padre de cada entrada;
este dump imprime el JSON crudo (nivel, flowId, parentFlow si existe) de:
  - la copia top-level
  - el subflow 'forms' de la copia
  - el subflow 'Password LoA'
  - el subflow 'Conditional OTP'
"""
import json
import sys
import urllib.parse

sys.path.insert(0, "scripts")

import probe_loa_flow as P  # noqa: E402


def dump(alias: str) -> None:
    print(f"\n===== executions de {alias!r} =====")
    raw = P.admin_ok(
        "GET",
        f"/admin/realms/{P.REALM}/authentication/flows/"
        f"{urllib.parse.quote(alias)}/executions")
    for e in json.loads(raw):
        print(json.dumps({k: e.get(k) for k in (
            "id", "displayName", "alias", "requirement", "priority",
            "providerId", "flowId", "parentFlow", "level", "authenticator",
            "authenticatorConfig", "authenticationConfig")}, ensure_ascii=False))


def main() -> int:
    P.admin("DELETE", f"/admin/realms/{P.REALM}")
    P.admin_ok("POST", "/admin/realms", {"realm": P.REALM, "enabled": True})
    P.restructure_flow()
    dump(P.FLOW)
    dump(f"{P.FLOW} forms")
    dump(P.PASSWORD_SUBFLOW)
    dump(f"{P.FLOW} Browser - Conditional OTP")
    P.admin("DELETE", f"/admin/realms/{P.REALM}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
