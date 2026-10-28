# UI Polish v2 — corrección de los 8 hallazgos de la crítica visual real (t_3cf085b9)

| Campo | Valor |
|---|---|
| **Fecha** | 2026-10-26/27 (r1-r2: run previo; r3: run final) |
| **Tarea** | t_3cf085b9 (ui) — corregir los 8 hallazgos de la crítica visual deepseek-v4.1-flash (login 4/10, Hermes Download 5.5/10) → meta ≥7/10 |
| **Stack vivo** | Admin Console :3100 (`trust-agent-console`, imagen reconstruida en cada ronda) · control plane :8000 · Keycloak 26.2 :8080 · PostgreSQL 17 |
| **Verificador** | deepseek-v4.1-flash (skill `skills/software-development/vision-check-maas/`) sobre capturas NUEVAS del navegador real (CDP full-page), sesión admin real PKCE+TOTP (acr=2) del tenant `hermes-dl` |

## Resultado final (capturas del build final, verificación 2026-10-27)

| Pantalla | Antes (2026-10-26) | Después (final) | Checks |
|---|---|---|---|
| **Login** (`01-app-login.png`) | **4/10** | **7.5/10** ✓ | 4/4 PASS (input sin rojo falso; CTA indigo confiado; Join/Create alineados y táctiles; contraste AA del panel oscuro) |
| **Hermes Download** (`02-admin-console-hermes-download.png`) | **5.5/10** | **8/10** ✓ | 4/4 PASS (URL con host con nombre; copy sin jerga; chip sin email truncado; un solo acento de marca, rojo solo destructivo) |

**Meta ≥7/10 en ambas: CUMPLIDA.** Detalle de la última corrida en
`vision-recheck.json` (respuestas íntegras del modelo vision).

## Los 8 hallazgos — qué se hizo

### Login (hallazgos 1-4)

1. **Borde rojo permanente del input de org code** → focus NEUTRAL
   (`border-foreground/60` + ring `foreground/12%`), validación al blur y el
   juicio de error solo al enviar o con `touched`+inválido. El rojo (danger)
   aparece únicamente con error real. Placeholder `e.g. acme-corp` a
   `text-muted` (AA) — antes `text-faint` ilegible.
2. **Botón primario "rojo desaturado"** → en r2 se saturó; en r3 se cambió la
   PALETA: el acento por defecto del console pasó de `#c90104` al **indigo
   `#4F46E5`**, el provider default REAL del branding (spec 004
   `PROVIDER_DEFAULT` — el API siempre lo devuelve para tenants sin branding
   propio). El rojo queda reservado para error/destructivo. Verificado en DOM:
   CTA computado `rgb(79, 70, 229)`.
3. **"Join/Create your organization" rotos (desborde)** → r2 los aplanó a
   enlaces; r3 los convirtió en **botones fantasma con borde** (affordance
   clara sin competir con la CTA), grid 2 col alineadas.
4. **Contraste del pie del panel oscuro** → escalado progresivo r2→r3:
   cuerpo `white/80`, lista `white/85`, pie `white/90` (verificado ~7:1 por
   el vision model).

### Hermes Download (hallazgos 5-8)

5. **URL `127.0.0.1:8000` visible** → el link se construye desde
   `TRUST_DOWNLOAD_BASE_URL` (compose ya lo cablea; `.env` local apunta a
   `http://download.trust-agent.local:8000`, host con nombre resuelto vía
   `/etc/hosts` que **responde 200 de verdad** — verificado con curl y desde
   el navegador). Documentado en `.env.example` para que el empaquetado
   (t_b62edff2) fije el dominio público real sin tocar código. El campo se
   muestra a contraste real (host `text-muted` + ruta bold), no como input
   gris deshabilitado. **Pendiente del despliegue**: dominio público + TLS
   (el vision model acepta el host con nombre como estado dev, pero pide
   `https://…` sin puerto para producción).
6. **Banner con jerga interna ("platform spec 023")** → copy reescrito
   orientado al usuario ("Link availability controls are coming soon…").
   Nota: el endpoint `GET /api/download-link` **ya existe** (HTTP 200 — spec
   023 desplegada por el lane dev entre ambas corridas), así que la pantalla
   final muestra el estado REAL (badge Enabled + toggle auditado); el banner
   queda como degradación honesta para cuando el contrato no exista.
7. **Email del header truncado duro** → el chip ya no muestra el email: usa
   `display_name` solo si es un nombre REAL (el control plane persiste
   `display_name = email` cuando no hay nombre, y eso re-introducía el email
   truncado — bug encontrado en r3); si no, la parte local (`admin`). Email
   completo en el menú con `truncate` CSS + `title`.
8. **Acento rojo agresivo del sidebar + iconografía** → ver hallazgo 2: un
   solo acento de marca. Además: item activo del sidebar con **tint del
   acento** (`bg-accent-soft` + texto accent + barra), iconos de pasos en
   círculos `size-9` con borde sutil, jerarquía de acciones por tarjeta
   ("Copy link" primario filled, "Copy invite message" secundario, "Open
   email draft" ghost). Verificado en DOM: activo `rgba(79,70,229,0.07)` /
   texto `rgb(79,70,229)`; "Disable link" rojo `rgb(185,28,28)` (único rojo
   de la página).

## Cambio estructural: el acento del chrome sigue al branding del tenant

- **Nuevo** `src/lib/branding-theme.ts`: valida hex `#rrggbb` (nada de CSS
  inyectable) y deriva la familia `--color-accent{,-hover,-soft,-border,-ring}`.
- `src/app/(console)/layout.tsx` lee `GET /api/branding` server-side con la
  sesión y inyecta el tema como `<style>` (cero flash, cero JS). Un tenant
  con branding publicado (p. ej. teal `#0F766E`) re-tematiza TODO el chrome
  — white-label real (spec 028), que era la raíz del hallazgo 8: el tile del
  logo ya usaba el acento del tenant (indigo) mientras el chrome seguía rojo.
- **Bug técnico hallado en r3**: `@theme inline` de Tailwind v4 INLITA los
  valores en las utilidades (`.bg-accent{background-color:#4f46e5}` literal),
  por lo que el override de `:root` no cambiaba NADA de lo renderizado — la
  primera "verificación" DOM (que solo leyó la var) dio un falso positivo.
  Corregido quitando `inline` (las utilidades referencian `var(--color-*)`);
  lección capturada en `docs/ui-standard.md` §4 y en el skill
  `vision-check-maas` (verificar colores COMPUTADOS de elementos, no vars).
- `docs/ui-standard.md` actualizado (token de acento + nota Tailwind). No es
  spec nueva: es ejecución del estándar existente + registro de la decisión.

## Verificación de calidad

- `npm run build` (console) → exit 0, 21 rutas; `npm run lint` → 0 problems.
- Imagen `trust-agent/console:local` reconstruida y contenedor recreado en
  CADA ronda (`docker compose up -d --no-deps console` — el one-shot `migrate`
  sigue roto, hallazgo ya documentado en t_360a752e §5, dueño: lane dev).
- Sesión admin real: reprovision idempotente del tenant `hermes-dl` (HTTP
  200), login PKCE S256 + segundo factor OTP real (acr=2), mint
  `POST /api/auth/session` (`mfa_verified=true`), whoami `org_admin`.
- Verificación DOM programática de cada fix (colores computados, textos,
  bordes) además de la crítica visual.

## Deuda honesta / coordinación

- **Dominio público del download link** (decisión de despliegue): la UI ya
  muestra lo que `TRUST_DOWNLOAD_BASE_URL` configure; t_b62edff2 debe fijar
  el dominio real (idealmente `https://` sin puerto, p. ej. tras un edge en
  :443). Variables documentadas en `.env.example`.
- **Header "machine-facing"** (mejora sugerida por el critic, fuera de los 8
  hallazgos): el topbar muestra el `tenant_slug` (`hermes-dl`) porque
  `/api/me` no expone el nombre de la organización, y el chip muestra `admin`
  porque el usuario no tiene `display_name` propio (persiste = email).
  Requiere un cambio menor del control plane (exponer `tenant_name` en
  whoami) — derivado a tarea propia.
  **→ RESUELTO en t_b14cbefb** (`docs/evidencia/header-human-facing/`):
  `tenant_name` aditivo en whoami, topbar con nombre de org + slug
  degradado, chip "Nombre — rol"; vision check 4/4 PASS, header 9/10.
- El vision model es no determinístico (±1-1.5 puntos entre corridas
  idénticas); los scores reportados son de la corrida final sobre las
  capturas finales, con verificación DOM que respalda cada PASS.
