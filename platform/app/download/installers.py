"""Artefactos de instalador por plataforma (spec 040, D-040-05/07/08).

Los instaladores desktop (NSIS ``.exe`` / AppImage / ``.dmg``) se construyen
EN EL HOST con ``scripts/build_desktop_installers.py`` (requieren node +
electron-builder + red — el contenedor api no los tiene, D-040-05) y quedan
en ``installer-artifacts/{slug}/{plugin_version}/`` junto a un
``installer-manifest.json`` escrito por el propio script:

    {
      "slug": "...", "plugin_version": "...", "built_at": "...",
      "git_rev": "...",
      "platforms": {
        "windows": {"filename": "...", "sha256": "...", "size": N},
        "linux":   {"filename": "...", "sha256": "...", "size": N}
      }
    }

Este módulo solo LEE: resuelve el directorio de la versión del plugin
ACTUAL del árbol (``plugin.yaml`` — D-040-07: nunca sirve un build viejo
sin decirlo) y expone los metadatos por plataforma. El serving lo hace el
router público con la misma seguridad que el bundle (rate limit,
anti-enumeración, fail-closed, audit por plataforma).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import get_settings
from .bundle import plugin_version

# Plataformas servidas (contrato §5.1 de la spec 040).
PLATFORMS = ("windows", "linux", "macos")

# Un solo MSIX genérico sirve a todas las organizaciones (ADR-003: el
# instalador es uno; el binding de org y el branding se resuelven en la
# activación contra el control plane). Los builds por tenant siguen
# funcionando y ganan precedencia cuando existen.
UNIVERSAL_SLUG = "_universal"


def _artifacts_root() -> Path:
    return Path(get_settings().installer_artifacts_dir)


def current_installer_dir(slug: str) -> Path:
    """Directorio de artefactos para la versión del plugin ACTUAL."""
    settings = get_settings()
    version = plugin_version(Path(settings.hermes_plugin_dir))
    return _artifacts_root() / slug / version


def _available_in_dir(directory: Path) -> dict[str, dict]:
    manifest_file = directory / "installer-manifest.json"
    if not manifest_file.is_file():
        return {}
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for platform, info in (manifest.get("platforms") or {}).items():
        if platform not in PLATFORMS or not info:
            continue
        artifact = directory / info["filename"]
        # Honestidad (constitution I): el botón solo se habilita si el
        # binario realmente está en disco.
        if artifact.is_file():
            out[platform] = {
                "filename": info["filename"],
                "sha256": info.get("sha256", ""),
                "size": info.get("size", artifact.stat().st_size),
            }
    return out


def available_installers(slug: str) -> dict[str, dict]:
    """Metadatos por plataforma del build actual ({} si no hay build).

    Precedencia: build por tenant si existe; si no, el MSIX universal
    ``_universal``. Así cualquier organización activa puede publicar el
    botón de descarga sin reconstruir un instalador por tenant.
    """
    for candidate in (slug, UNIVERSAL_SLUG):
        out = _available_in_dir(current_installer_dir(candidate))
        if out:
            return out
    return {}


def installer_path(slug: str, platform: str) -> Path | None:
    """Ruta del artefacto servido para (slug, platform) — None si no hay."""
    for candidate in (slug, UNIVERSAL_SLUG):
        directory = current_installer_dir(candidate)
        info = _available_in_dir(directory).get(platform)
        if info:
            return directory / info["filename"]
    return None
