#!/usr/bin/env python3
"""Build the marketplace's factory profiles as REAL Hermes profile exports (spec 045, D-045-A).

Analyst, Coder and Ops are created with Hermes' own profile API — the code behind
``hermes profile create`` / ``hermes profile export`` — inside a throw-away HOME/HERMES_HOME
(the real ~/.hermes is never touched), given a role SOUL.md and a curated set of skills copied
from Hermes' bundled catalog, and exported. The exports land in
``app/profile_marketplace/factory/`` with ``factory.json``; at runtime they go through the same
import pipeline as any admin upload (``app.profile_marketplace.sanitize``).

Usage (host with a Hermes checkout):
    python3 scripts/build_factory_profiles.py [--hermes-root /usr/local/lib/hermes-agent]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent
OUT_DIR = PLATFORM / "app" / "profile_marketplace" / "factory"

FACTORY = [
    {
        "key": "analyst",
        "name": "Analyst",
        "name_es": "Analista",
        "description": "Research and data analysis: cited findings, spreadsheets and decision-ready reports.",
        "description_es": "Investigación y análisis de datos: hallazgos citados, hojas de cálculo e informes para decidir.",
        "skills": [
            "research/grounded-citations",
            "productivity/microsoft-office",
            "productivity/pdf",
            "productivity/document-to-action-items",
        ],
        "soul": """# Analyst

You are an analyst agent for your organization. You help people research questions, analyze data
and turn findings into clear, decision-ready reports.

## How you work
- Start from the question: restate it, note your assumptions and what evidence would change the answer.
- Ground every claim: cite the document, dataset or URL it comes from, and say plainly when evidence is missing or weak.
- Analyze before you summarize: compute, compare and check the numbers in spreadsheets, CSVs and reports instead of estimating.
- Deliver for decisions: lead with the conclusion, then the supporting evidence, then caveats and next steps.
- Keep confidential data inside the tools and connections your organization approved.

Respond in the language the user writes in.
""",
    },
    {
        "key": "coder",
        "name": "Coder",
        "name_es": "Desarrollador",
        "description": "Software development: code changes, debugging, tests and reviews inside the repo.",
        "description_es": "Desarrollo de software: cambios de código, depuración, pruebas y revisiones en el repositorio.",
        "skills": [
            "software-development/test-driven-development",
            "software-development/systematic-debugging",
            "software-development/requesting-code-review",
            "software-development/codebase-inspection",
        ],
        "soul": """# Coder

You are a software engineering agent for your organization. You help developers understand, change
and verify code inside their repositories.

## How you work
- Read before you write: locate the relevant files, trace symbols to their definitions and match the project's conventions.
- Make small, reviewable changes, and explain what changed and why.
- Verify: run the project's tests, linters and builds, and report the real results. Never claim a success you did not observe.
- For new behavior and bug fixes, prefer tests first: reproduce, fix, prove.
- Never commit, push or rewrite history unless asked, and never expose secrets or credentials.

Respond in the language the user writes in.
""",
    },
    {
        "key": "ops",
        "name": "Ops",
        "name_es": "Operaciones",
        "description": "IT operations: incident triage, health checks, log analysis and operational reports.",
        "description_es": "Operaciones de TI: triage de incidentes, salud de servicios, análisis de logs e informes.",
        "skills": [
            "software-development/systematic-debugging",
            "productivity/document-to-action-items",
            "productivity/meeting-action-items",
            "productivity/microsoft-office",
        ],
        "soul": """# Ops

You are an IT operations agent for your organization. You help teams triage incidents, check the
health of infrastructure and services, analyze logs and report on operations.

## How you work
- Triage first: establish impact, scope and timeline before proposing fixes.
- Evidence over guesses: read logs, metrics and configuration, and quote the lines that support a conclusion.
- Prefer read-only diagnostics. Propose changes with their risk and rollback, and ask before anything disruptive or irreversible.
- Write operational reports a teammate can act on: what happened, current state, root cause or hypotheses, next actions.
- Respect change windows and your organization's approval rules.

Respond in the language the user writes in.
""",
    },
]

# Runs inside Hermes' own interpreter: the same functions `hermes profile create/export` call.
_DRIVER = r"""
import json, shutil, sys
from pathlib import Path
from hermes_cli.profiles import create_profile, export_profile

spec = json.loads(sys.stdin.read())
bundled = Path(spec["bundled_skills"])
for item in spec["profiles"]:
    profile_dir = create_profile(
        item["key"], no_alias=True, no_skills=True,
        description=item["description"], soul=item["soul"],
    )
    for rel in item["skills"]:
        src = bundled / rel
        if not (src / "SKILL.md").is_file():
            raise SystemExit(f"bundled skill not found: {rel}")
        shutil.copytree(src, Path(profile_dir) / "skills" / rel)
    out = export_profile(item["key"], str(Path(spec["out_dir"]) / f"{item['key']}.tar.gz"))
    print(f"exported {item['key']} -> {out}")
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the marketplace factory profiles as real Hermes exports.")
    parser.add_argument("--hermes-root", default="/usr/local/lib/hermes-agent")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    hermes_root = Path(args.hermes_root)
    python = next(
        (p for p in (hermes_root / "venv/bin/python", hermes_root / ".venv/bin/python") if p.is_file()),
        None,
    )
    if python is None:
        print(f"no Hermes interpreter under {hermes_root}", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="trust-factory-") as tmp:
        home = Path(tmp)
        (home / ".hermes").mkdir()
        staging = home / "exports"
        staging.mkdir()
        env = {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
        }
        spec = {
            "bundled_skills": str(hermes_root / "skills"),
            "out_dir": str(staging),
            "profiles": [
                {k: item[k] for k in ("key", "description", "soul", "skills")} for item in FACTORY
            ],
        }
        result = subprocess.run(
            [str(python), "-c", _DRIVER], input=json.dumps(spec), text=True,
            capture_output=True, env=env, cwd=str(hermes_root), timeout=600,
        )
        sys.stdout.write(result.stdout)
        if result.returncode != 0:
            sys.stderr.write(result.stderr[-4000:])
            return result.returncode

        catalog = []
        for item in FACTORY:
            archive = staging / f"{item['key']}.tar.gz"
            target = out_dir / archive.name
            shutil.copyfile(archive, target)
            catalog.append({
                "key": item["key"],
                "name": item["name"],
                "name_es": item["name_es"],
                "description": item["description"],
                "description_es": item["description_es"],
                "archive": archive.name,
                "export_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            })
    (out_dir / "factory.json").write_text(json.dumps({"profiles": catalog}, indent=2) + "\n")
    print(f"factory catalog -> {out_dir / 'factory.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
