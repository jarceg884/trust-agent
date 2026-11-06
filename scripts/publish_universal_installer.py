#!/usr/bin/env python3
"""Publish a universal Trust Agent MSIX for every tenant.

The MSIX built from the neutral bundle contains no tenant secret or binding;
employees bind it to their organization at activation with a one-time token.
The control plane falls back to ``installer-artifacts/_universal/<version>/``
when a tenant has no dedicated installer.

Usage:
  python3 scripts/publish_universal_installer.py \
      --msix /path/TrustAgentDesktop-0.0.0-win-x64.msix --version 0.6.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msix", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifacts-dir", type=Path, default=REPO / "installer-artifacts")
    args = parser.parse_args()

    if not args.msix.is_file():
        raise SystemExit(f"MSIX not found: {args.msix}")
    target = args.artifacts_dir / "_universal" / args.version
    target.mkdir(parents=True, exist_ok=True)
    dest = target / args.msix.name
    if args.msix.resolve() != dest.resolve():
        dest.write_bytes(args.msix.read_bytes())

    digest = sha256(dest)
    manifest = {
        "slug": "_universal",
        "plugin_version": args.version,
        "built_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "desktop_app": "Trust Agent universal MSIX — one governed desktop for every organization; tenant binding happens at activation.",
        "platforms": {
            "windows": {
                "filename": dest.name,
                "sha256": digest,
                "size": dest.stat().st_size,
            }
        },
    }
    manifest_path = target / "installer-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "sha256": digest, "size": dest.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
