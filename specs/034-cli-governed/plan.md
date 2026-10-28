# Plan técnico — Spec 034 (cli-governed)

## Cambios por archivo

### Control plane (Python/FastAPI)

1. **`db/migrations/versions/20261027_0025_device_type.py`** (NUEVO, vía
   `migration_guard.py new --slug device_type`): `add_column devices.device_type
   text NOT NULL DEFAULT 'desktop' CHECK IN ('desktop','cli')`. Paridad con
   `db/schema.sql` §5.9 (columna al final de la tabla). Rebuild imagen migrate
   + `docker compose up -d`.
2. **`platform/app/devices/router.py`**: `EnrollBody.device_type: str = "desktop"`,
   validación `in ("desktop","cli")` → 422; pasa a `service.enroll`.
3. **`platform/app/devices/service.py`**: `enroll()` INSERT/UPDATE con
   `device_type`; `_DEVICE_SELECT`/`_PRESENCE_SELECT`/`my_devices`/`_serialize`
   exponen `device_type`.
4. **`platform/app/download/bundle.py`**: nuevo miembro `{root}/skin.yaml`
   (fuente única del skin para el instalador CLI — D-034-02). El manifest
   lo registra en `members_sha256` automáticamente.
5. **`platform/app/download/cli.py`** (NUEVO): `build_cli_installer(...)` →
   script bash por tenant. Hornea slug/api_base/litellm/model; descarga el
   bundle canónico y extrae plugin+trust-login+skin; binding org-code.txt;
   `.env` con `TRUST_AGENT_API`, `TRUST_ORG_CODE`, `TRUST_DEVICE_TYPE=cli`;
   `hermes config set` idénticos al desktop; `--home DIR`.
6. **`platform/app/download/router.py`**: `GET /download/{slug}/cli` público
   (throttle `dl:cli:{slug}:{ip}`, anti-enumeración, fail-closed, audit
   `download.issued` con `artifact:"cli"`); `download_page` pasa `api_base`
   al renderer.
7. **`platform/app/download/page.py`**: `render_page(..., api_base)` — dos
   opciones (Desktop / CLI con comando curl copiable + requisitos
   Linux/macOS/WSL2), branding en ambas.

### Plugin (nuestro, no core de Hermes)

8. **`platform/hermes/plugins/trust-agent-governance/enroll.py`**: `_enroll_flow`
   envía `device_type` = env `TRUST_DEVICE_TYPE` (default `"desktop"`).

### Console (Next.js)

9. **`platform/console/src/lib/types.ts`**: `Device.device_type`,
   `PresenceDevice.device_type`.
10. **`platform/console/src/app/(console)/hermes-download/hermes-download-page.tsx`**:
    tarjeta "CLI installation" con `curl -fsSL {url}/cli | bash` + CopyButton;
    invite message menciona ambas opciones.
11. **`platform/console/src/app/(console)/devices/devices-page.tsx`**: badge
    CLI/Desktop junto al nombre del dispositivo.

### Tests y evidencia

12. **`platform/tests/test_cli_governed.py`** (NUEVO): endpoint /cli
    (200 script con org_code horneado, audit +1 artifact=cli, disabled → 403,
    suspendido → 403, slug desconocido → 404, rate limit), enroll con
    device_type cli/desktop/inválido, exposición en list/presence/me,
    miembro skin.yaml en el bundle.
13. **`platform/scripts/evidence_cli_governed.py`** (NUEVO): E2E live por
    fases (setup/download/install/firstrun/approve/governed/revoke) contra el
    stack real (:8000/:8080/:4000), reutilizando los helpers verificados de
    `e2e_final_live.py`. Evidencia → `docs/evidencia/cli-governed/`.
14. **`docs/cli-governed.md`** (NUEVO, español): flujo, endpoints, decisiones.

## Orden de implementación

migración → service/router devices → bundle/cli/router/page → plugin →
console → tests → migrate rebuild → E2E live → docs.

## Riesgos

- **Hotspot migraciones**: ya anunciado en la card (comentario); migration_guard
  serializa contra el head real.
- **Rate limit del bundle en el E2E**: el instalador CLI descarga el bundle
  (max 5/min por IP) — el harness limpia `ops.login_throttle` al inicio de
  la fase download (mismo patrón del evidence de spec 023).
- **`hermes chat` en tmux**: mismo patrón verificado del evidence spec 023
  (gateway standalone: SOLO el chat, nunca gateway explícito adicional).
