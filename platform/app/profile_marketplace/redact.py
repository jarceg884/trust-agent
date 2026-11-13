"""Secret redaction for imported profile files (spec 045 §2).

Defense in depth: ``hermes profile export`` already force-redacts its staged copy, but an upload
is untrusted (a hand-made tar, an old Hermes, a distribution repo). The high-precision subset of
Hermes' ``agent/redact.py`` is ported here — vendor key prefixes, private keys, JWTs, secret-named
assignments, credentials in URLs and auth headers.

Skills are code and documentation, so precision beats recall: a secret-named assignment is
redacted only when its value looks like a literal secret (no spaces or brackets, ≥ 12 chars,
letters AND digits), never ``os.environ["X"]``, ``${VAR}``, ``<your-key>`` or ``YOUR_TOKEN``.
Every pattern is linear: keys are bounded, values stop at delimiters.
"""
from __future__ import annotations

import re

# A plain token on purpose: valid unquoted in YAML/dotenv/code and inside JSON strings.
# ``[REDACTED]`` unquoted in YAML parses as a LIST and would change a config's types.
REDACTED = "REDACTED"

# Vendor key prefixes (agent/redact.py _PREFIX_PATTERNS): prefix literal + opaque body.
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-](?:\.?[A-Za-z0-9_-]){9,}",
    r"ctx7sk-[A-Za-z0-9_-]{10,}",
    r"ghp_[A-Za-z0-9]{10,}",
    r"github_pat_[A-Za-z0-9_]{10,}",
    r"gh[ousr]_[A-Za-z0-9]{10,}",
    r"xapp-\d+-[A-Za-z0-9-]{10,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AIza[A-Za-z0-9_-]{30,}",
    r"pplx-[A-Za-z0-9]{10,}",
    r"fal_[A-Za-z0-9_-]{10,}",
    r"fc-[A-Za-z0-9]{10,}",
    r"bb_live_[A-Za-z0-9_-]{10,}",
    r"AKIA[A-Z0-9]{16}",
    r"[sr]k_(?:live|test)_[A-Za-z0-9]{10,}",
    r"SG\.[A-Za-z0-9_-]{10,}",
    r"hf_[A-Za-z0-9]{10,}",
    r"r8_[A-Za-z0-9]{10,}",
    r"npm_[A-Za-z0-9]{10,}",
    r"pypi-[A-Za-z0-9_-]{10,}",
    r"do[op]_v1_[A-Za-z0-9]{10,}",
    r"sk_[A-Za-z0-9_]{10,}",
    r"tvly-[A-Za-z0-9]{10,}",
    r"exa_[A-Za-z0-9]{10,}",
    r"gsk_[A-Za-z0-9]{10,}",
    r"xai-[A-Za-z0-9]{30,}",
    r"ntn_[A-Za-z0-9]{10,}",
    r"glpat-[A-Za-z0-9_\-]{10,}",
    r"gl(?:oas|dt|cbt|ptt|ft|imt|soat|ffct|wt)-[A-Za-z0-9_\-]{10,}",
    r"glrtr?-[A-Za-z0-9_.\-]{10,}",
]
_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_=-]{10,}")
# scheme://user:password@host — any scheme (http, postgres, redis, amqp, mongodb+srv…).
_URL_USERINFO_RE = re.compile(r"\b([a-z][a-z0-9+.-]{0,20}://[^/\s:@'\"]{1,256}):([^/\s@'\"]{1,256})@", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(
    r"((?:Proxy-)?Authorization\s*:\s*)((?:Bearer|Basic|Token)\s+)?([A-Za-z0-9._~+/=:-]{12,4096})",
    re.IGNORECASE,
)
_SECRET_KEYWORD_RE = re.compile(
    r"api[_-]?key|apikey|token|secret|passw(?:or)?d|private[_-]?key|access[_-]?key|credential",
    re.IGNORECASE,
)
# KEY=value / key: value (YAML, dotenv, TOML, INI, shell) — key bounded, value stops at delimiters.
_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<key>[A-Za-z0-9_.-]{1,64})(?P<sep>[ \t]*[:=][ \t]*)"
    r"(?P<q>['\"]?)(?P<val>[^\s'\",#;()\[\]{}<>]{8,4096})(?P=q)"
)
_JSON_RE = re.compile(r"\"(?P<key>[A-Za-z0-9_.-]{1,64})\"(?P<sep>\s*:\s*)\"(?P<val>[^\"\\\s]{8,4096})\"")
_LITERAL_RE = re.compile(r"^[A-Za-z0-9_\-+/=.:~]{12,}$")
_PLACEHOLDER_RE = re.compile(
    r"^(?:\$|<|\{\{|%|\*{3}|\[?REDACTED|x{6,}|your[_-]|changeme|example|placeholder|replace[_-]?me)",
    re.IGNORECASE,
)


def _looks_secret(value: str) -> bool:
    return (
        bool(_LITERAL_RE.match(value))
        and any(c.isdigit() for c in value)
        and any(c.isalpha() for c in value)
        and not _PLACEHOLDER_RE.match(value)
    )


def redact_text(text: str) -> tuple[str, int]:
    """Return ``(redacted_text, count)``. Idempotent: redacting twice changes nothing."""
    count = 0

    def sub(pattern: re.Pattern, repl, value: str) -> str:
        def _wrap(match: re.Match) -> str:
            nonlocal count
            result = repl(match)
            if result != match.group(0):
                count += 1
            return result

        return pattern.sub(_wrap, value)

    def assignment(match: re.Match) -> str:
        if not _SECRET_KEYWORD_RE.search(match.group("key")) or not _looks_secret(match.group("val")):
            return match.group(0)
        return f'{match.group("key")}{match.group("sep")}{match.group("q")}{REDACTED}{match.group("q")}'

    def json_field(match: re.Match) -> str:
        if not _SECRET_KEYWORD_RE.search(match.group("key")) or not _looks_secret(match.group("val")):
            return match.group(0)
        return f'"{match.group("key")}"{match.group("sep")}"{REDACTED}"'

    def auth_header(match: re.Match) -> str:
        if not _looks_secret(match.group(3)):
            return match.group(0)
        return f"{match.group(1)}{match.group(2) or ''}{REDACTED}"

    text = sub(_PRIVATE_KEY_RE, lambda m: REDACTED, text)
    text = sub(_PREFIX_RE, lambda m: REDACTED, text)
    text = sub(_JWT_RE, lambda m: REDACTED, text)
    text = sub(_URL_USERINFO_RE, lambda m: f"{m.group(1)}:{REDACTED}@", text)
    text = sub(_AUTH_HEADER_RE, auth_header, text)
    text = sub(_JSON_RE, json_field, text)
    text = sub(_ASSIGN_RE, assignment, text)
    return text, count
