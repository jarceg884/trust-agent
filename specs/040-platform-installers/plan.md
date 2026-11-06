# Plan — Spec 040 (t_fff52ec1)

Orden de implementación (spec-first, nada simulado):

1. **Realidad de build (ya verificada)** — smoke test electron-builder en este host:
   AppImage ✅, NSIS .exe ✅ (requiere `wine64`, instalado), macOS .dmg ❌ (gap + plan
   runner). Evidencia en `docs/evidencia/desktop-installers/`.
2. **`platform/installer-app/`** — wizard Electron (main + renderer plano, sin
   toolchain de build JS): welcome → install (Hermes oficial + bundle canónico +
   binding + skin + config) → sign-in OIDC embebido → done/launch. Resources
   `org-binding.json` + `branding.json` inyectados al build (D-040-02).
3. **`scripts/build_desktop_installers.py`** — orquesta el build host-side por
   tenant: lee tenant/branding de la DB canónica, inyecta resources, corre
   electron-builder (nsis + AppImage), deja artefactos + `installer-manifest.json`
   en `installer-artifacts/{slug}/{plugin_version}/`.
4. **API** — `platform/app/download/installers.py` (escaneo de artefactos) +
   endpoints `GET /download/{slug}/installer/{platform}` en el router público
   (rate limit, anti-enumeración, fail-closed, audit por plataforma) + settings
   (`installer_artifacts_dir`).
5. **Página** — `page.py` rediseñada: 3 botones por plataforma (OS detect
   client-side que resalta el botón del visitante), estados honestos, sección CLI
   intacta.
6. **Compose** — volume `./installer-artifacts` → `/opt/trust-agent/installer-artifacts:ro`
   en el servicio api.
7. **Build real** para el tenant de evidencia (windows + linux) con el script.
8. **E2E** — AppImage en este servidor (flujo completo real) + verificación 7z del
   .exe + handoff a Miller. Evidencia en `docs/evidencia/desktop-installers/`.
9. **Tests** — pytest de endpoints/audit/página (TRUST_TEST_DB aislado).
10. **Docs** — `docs/desktop-installers.md` (español) + spec marcada CONVERGIDA.

Riesgos conocidos:
- wine es lento la primera vez (wineprefix) — build NSIS puede tardar minutos.
- Disco: los artefactos pesan ~100-200 MB por plataforma; el directorio
  `installer-artifacts/` queda fuera de git (.gitignore).
- Sin firma Authenticode: el .exe muestra SmartScreen warning en Windows (gap
  documentado, no simulado).
