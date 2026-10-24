#!/usr/bin/env python3
"""Launcher del doble HTTP declarado de LiteLLM (D-GOV-15) en puerto FIJO.

Sirve para el empaquetado local (docker-compose `litellm-double`): el control
plane lo consume como TRUST_LITELLM_BASE_URL. DOBLE DECLARADO (constitution I):
NO es el proxy real de LiteLLM; implementa exactamente el contrato documentado
de la Admin API que consume `app/models/litellm.py`
(https://docs.litellm.ai/docs/proxy/virtual_keys). Contra un proxy real solo
cambia TRUST_LITELLM_BASE_URL (y TRUST_LITELLM_MASTER_KEY) del control plane.

El puerto se pinea patcheando ThreadingHTTPServer: la clase original binda
port 0 (aleatorio) porque es un doble de tests.
"""
import argparse
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # platform/ (paquete app/)
sys.path.insert(0, str(HERE.parent / "tests"))  # tests/ (litellm_fake)

import litellm_fake  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run_litellm_double",
        description="Doble HTTP declarado de la LiteLLM Admin API en puerto fijo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39989)
    args = parser.parse_args()

    class _FixedPortHTTPServer(ThreadingHTTPServer):
        """Igual que ThreadingHTTPServer pero binda el puerto pedido.

        La clase original binda port 0 = aleatorio (uso en tests); el
        empaquetado local necesita el puerto pineado (39989 canónico).
        """

        def __init__(self, server_address, RequestHandlerClass,
                     bind_and_activate=True):
            super().__init__((args.host, args.port), RequestHandlerClass,
                             bind_and_activate)

    litellm_fake.ThreadingHTTPServer = _FixedPortHTTPServer
    fake = litellm_fake.LiteLLMFake()
    print(f"LITELLM_DOUBLE_READY base_url={fake.base_url} "
          f"master_key=sk-master-test (doble declarado, D-GOV-15)", flush=True)
    assert fake.base_url == f"http://{args.host}:{args.port}", fake.base_url
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
