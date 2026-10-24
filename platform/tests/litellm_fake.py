"""Honest HTTP test double of the LiteLLM proxy Admin API (D-GOV-15).

DECLARED TEST DOUBLE — this is NOT a LiteLLM proxy; it is a real HTTP
server implementing exactly the documented Admin API contract that
`app/models/litellm.py` consumes
(https://docs.litellm.ai/docs/proxy/virtual_keys):

- POST /key/generate  -> {key (plaintext once), token (sha256 of key), ...}
- POST /key/block     -> accepts the sha256 hash as "key" (verified against
                         production LiteLLM behaviour)
- POST /key/unblock   -> same
- GET  /key/info?key= -> key record
- GET  /spend/logs/v2 -> paginated logs with request_id, spend (USD),
                         startTime, tokens and the spend_logs_metadata
                         stamped at key generation
- POST /chat/completions -> the DATA PLANE: enforces the documented
                         gateway semantics — blocked key -> 401, budget
                         exceeded -> 429 (budget_exceeded_status_code: 429),
                         model outside the key's list -> 403 — and
                         accumulates spend priced from the platform's own
                         catalog prices passed at startup.

Same pattern as the mock OIDC IdP of spec 003: real HTTP, documented
semantics, no pretense of being the real proxy.
"""
import hashlib
import json
import secrets
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MASTER_KEY = "sk-master-test"


class LiteLLMFake:
    def __init__(self, prices: dict[str, tuple[int, int]] | None = None):
        # model -> (input_cents_per_m, output_cents_per_m)
        self.prices = prices or {
            "qwen2.5-72b-instruct": (400, 1200),
            "deepseek-v3": (270, 1090),
        }
        self.keys: dict[str, dict] = {}       # sha256 -> record
        self.logs: list[dict] = []
        self.block_calls: list[str] = []
        self.unblock_calls: list[str] = []
        self.generate_calls: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status: int, body: dict):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def _master_authed(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {MASTER_KEY}"

            def do_GET(self):  # noqa: N802
                if not self._master_authed():
                    self._json(401, {"error": "invalid master key"})
                    return
                if self.path.startswith("/key/info"):
                    key = self.path.split("key=")[-1]
                    rec = outer.keys.get(key)
                    if rec is None:
                        self._json(404, {"error": "Key not found."})
                        return
                    self._json(200, {"key": rec["public"]})
                    return
                if self.path.startswith("/spend/logs/v2"):
                    q = parse_qs(urlparse(self.path).query)
                    start = q.get("start_date", [""])[0]
                    page = int(q.get("page", ["1"])[0])
                    size = int(q.get("page_size", ["100"])[0])
                    rows = [l for l in outer.logs
                            if not start or l["startTime"][:10] >= start]
                    window = rows[(page - 1) * size: page * size]
                    self._json(200, {"data": window,
                                     "total": len(rows), "page": page})
                    return
                if self.path.startswith("/health/liveliness"):
                    self._json(200, {"status": "healthy"})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                body = self._body()

                if self.path == "/chat/completions":
                    # DATA PLANE — authenticates with the VIRTUAL key.
                    auth = self.headers.get("Authorization", "")
                    token = hashlib.sha256(
                        auth.replace("Bearer ", "").encode()
                    ).hexdigest()
                    rec = outer.keys.get(token)
                    if rec is None:
                        self._json(401, {"error": {"message": "Invalid key"}})
                        return
                    pub = rec["public"]
                    if pub["blocked"]:
                        self._json(401, {"error": {
                            "message": "Key is blocked",
                            "type": "auth_error",
                        }})
                        return
                    model = body.get("model", "")
                    if pub["models"] and model not in pub["models"]:
                        self._json(403, {"error": {
                            "message": f"Model {model} not allowed for key",
                        }})
                        return
                    if pub["max_budget"] is not None and \
                            pub["spend"] >= pub["max_budget"]:
                        # Documented native behaviour with
                        # budget_exceeded_status_code: 429 (ADR-014).
                        self._json(429, {"error": {
                            "message": "Budget exceeded",
                            "type": "budget_exceeded",
                        }})
                        return
                    tin, tout = 100_000, 50_000  # deterministic token counts
                    cin, cout = outer.prices.get(model, (400, 1200))
                    # USD: tokens * cents_per_million / 1e6 / 100
                    cost_usd = (tin * cin + tout * cout) / 1_000_000 / 100
                    pub["spend"] += cost_usd
                    request_id = str(uuid.uuid4())
                    outer.logs.append({
                        "request_id": request_id,
                        "user_api_key_hash": token,
                        "model": model,
                        "spend": cost_usd,
                        "tokens": tin,
                        "prompt_tokens": tin,
                        "completion_tokens": tout,
                        "startTime": datetime.now(timezone.utc).isoformat(),
                        "metadata": pub["metadata"],
                    })
                    self._json(200, {
                        "id": request_id,
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": tin,
                                  "completion_tokens": tout},
                    })
                    return

                # ADMIN PLANE — master key required.
                if not self._master_authed():
                    self._json(401, {"error": "invalid master key"})
                    return
                if self.path == "/key/generate":
                    plaintext = "sk-fake-" + secrets.token_hex(20)
                    token = hashlib.sha256(plaintext.encode()).hexdigest()
                    record = {
                        "public": {
                            "key_alias": body.get("key_alias"),
                            "user_id": body.get("user_id"),
                            "models": body.get("models", []),
                            "max_budget": body.get("max_budget"),
                            "budget_duration": body.get("budget_duration"),
                            "metadata": body.get("metadata", {}),
                            "blocked": False,
                            "spend": 0.0,
                        },
                        "plaintext_once": plaintext,
                    }
                    outer.keys[token] = record
                    outer.generate_calls.append(body)
                    self._json(200, {"key": plaintext, "token": token,
                                     "key_alias": body.get("key_alias")})
                    return
                if self.path in ("/key/block", "/key/unblock"):
                    token = body.get("key")
                    rec = outer.keys.get(token)
                    if rec is None:
                        self._json(404, {"error": "Key not found."})
                        return
                    blocking = self.path == "/key/block"
                    rec["public"]["blocked"] = blocking
                    (outer.block_calls if blocking else outer.unblock_calls)\
                        .append(token)
                    self._json(200, {"blocked": blocking})
                    return
                self._json(404, {"error": "not found"})

            def log_message(self, *args):  # silence
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def chat(self, key: str, model: str) -> tuple[int, dict]:
        """Direct data-plane call helper for tests."""
        import httpx

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user",
                                                "content": "hi"}]},
            timeout=10.0,
        )
        return resp.status_code, (resp.json() if resp.content else {})

    def shutdown(self) -> None:
        self._server.shutdown()
