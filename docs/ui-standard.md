# Estándar UI — Trust Agent (mandato Miller, t_7cd4f40e)

> **"Todo más pro — iconos de una librería pro, todo fluido, animaciones si es
> necesario."** Este documento es OBLIGATORIO para TODA tarea UI de la
> plataforma (Admin Console, Provider Console, desktop, página pública de
> descarga, pantallas de login/MFA/auto-registro), incluidas las tareas en
> vuelo. Español para comentarios; UI copy en inglés.

Stack: **Next.js (App Router) + TypeScript + Tailwind CSS**. Design system
existente: `components/ui.tsx` + tokens de color en `globals.css` (tema claro
enterprise para las consolas, tema dark para el desktop). **No se rediseña
desde cero: se extiende.**

## 1. Iconos: lucide-react, nada de SVG casero

- La dependencia **`lucide-react`** está instalada en las 3 apps
  (`platform/console`, `platform/provider-console`).
- `components/icons.tsx` (console y provider) es una **capa de compatibilidad**
  que re-exporta iconos lucide con los defaults del sistema. Úsala con los
  nombres existentes (`IconOverview`, `IconBudgets`, …) o importa directo de
  `"lucide-react"` para iconos nuevos.
- **Defaults fijos del sistema** (ya aplicados por la capa):
  - `strokeWidth: 1.75` — stroke uniforme en TODA la app (densidad enterprise).
  - `size: 16` por defecto; 13 en botones `sm`, 18 en ítems de sidebar.
  - `aria-hidden: true` (el icono es decorativo; el texto accesible va al lado).
  - Alineación óptica: el contenedor del icono lleva `shrink-0` y el texto
    `leading` coherente; nunca iconos flotantes sin `flex items-center`.
- Prohibido: SVG inline casero, glifos de texto (▤ ✦ ◉), emojis como iconos,
  mezclar sets de iconos.

## 2. Fluidez: transiciones en todos los estados

- **Todo estado interactivo** (hover, focus, active, disabled) tiene
  transición: `transition-all duration-150 ease-out` es el default de Button;
  inputs llevan `transition-colors duration-150`.
- **Feedback de click**: `active:scale-[0.98]` en botones (desactivado en
  estado disabled).
- **Focus visible**: ya es global (`:focus-visible` con outline del acento) —
  no lo tapes con `outline-none` sin reponerlo.
- **Carga**: skeletons (`<Skeleton>` / `<TableSkeleton>`) reservando el espacio
  real — NADA de saltos de layout (CLS). Spinners solo dentro de botones
  (`loading`).
- **Tablas**: hover de fila con transición de color (global en `globals.css`:
  `tbody tr { transition: background-color 150ms ease } tbody tr:hover`).
- **Diálogos**: entrada scale+fade ~180ms (clases `ta-dialog-backdrop` /
  `ta-dialog-panel`, ya en `Dialog`). Salida: cierre inmediato aceptable.
- **Toasts**: entrada deslizante (`ta-toast-in`, ya en `ToastProvider`).
- **Optimistic updates** solo donde el rollback es barato y visible (p. ej.
  toggle de estado); si duda, skeleton + toast.

## 3. Animaciones con propósito (no decorativas)

Keyframes compartidos en `globals.css` de cada app — **úsalas, no inventes
otras** sin necesidad:

| Clase | Efecto | Uso típico |
|---|---|---|
| `ta-fade-in` | fade 180ms | apariciones sutiles |
| `ta-rise` | fade + sube 10px, 400ms | tarjetas de una página de flujo, con retardo escalonado |
| `ta-dialog-backdrop` / `ta-dialog-panel` | fade / scale+fade 180ms | diálogos (automático en `Dialog`) |
| `ta-toast-in` | slide desde la derecha 220ms | toasts (automático) |
| `ta-pop` | pop con overshoot 250ms | badges de estado (automático en `Badge`) |

- **Presupuesto/barras de progreso**: `transition-[width] duration-500 ease-out`
  (ya en `Progress` / `ProgressBar`) — la barra "crece", no salta.
- **Página**: fade sutil al montar `main` (global). Para páginas de flujo,
  escalona con `ta-rise` y `animation-delay` por índice (patrón de
  `/hermes-download`).
- **Acciones destructivas**: SIEMPRE `ConfirmDialog` (Enter confirma, Esc
  cierra — ya implementado); el botón peligroso es `variant="danger"`.
- **Atajos de teclado en diálogos**: Esc cierra (global en `Dialog`); Enter
  confirma en `ConfirmDialog` salvo que el foco esté en otro botón.
- **framer-motion SOLO** si algo no se logra con CSS/Tailwind. Hoy nada lo
  necesita.
- **`prefers-reduced-motion`**: TODA animación vive bajo
  `@media (prefers-reduced-motion: no-preference)` — nunca animes al margen
  de esta media query. Las transiciones de color (hover) quedan fuera porque
  no son movimiento.

## 4. Consistencia: un solo sistema

- Mismo `ui.tsx` (Button, Card, Badge, Field, Input, Table, Skeleton,
  EmptyState, ErrorState, Dialog, ConfirmDialog, CopyButton, OneTimeSecret,
  Progress, Pagination, PageHeader, ToastProvider) en console y
  provider-console; el desktop mantiene su variante dark (`ui.tsx` propio con
  los mismos nombres donde aplica).
- Tokens de color del tema claro: fondo `#f8f9fb`, superficies `#ffffff`,
  acento `#4F46E5` (provider default del branding, spec 004 — el mismo que
  devuelve `GET /api/branding`; el layout del console re-tematiza los tokens
  `--color-accent*` con el `accent_color` publicado del tenant, spec 028
  white-label). El ROJO (`--color-danger` `#b91c1c`) queda reservado para
  error/destructivo — nunca para CTA primario ni estados seleccionados
  (crítica visual t_3cf085b9: un acento rojo se lee como alarma).
  Tema dark del desktop: fondo `#0e1318`, acento `#f91128`.
  No inventes colores: usa los tokens (`text-muted`, `bg-surface-alt`, …).
  Nota Tailwind v4: `@theme` SIN `inline` en el console — las utilidades
  deben referenciar `var(--color-*)` para que el re-tema runtime del tenant
  funcione (con `inline` los valores quedan literales y el override no pega).
- Si un componente nuevo puede vivir en `ui.tsx`, vive ahí — no lo dupliques
  por página.

## 5. Detalles pro

- **Empty states**: `EmptyState` con icono lucide + título + descripción + CTA.
- **Badges de estado**: `Badge` (con `ta-pop` automático). Para que el pop
  se reproduzca al CAMBIAR el estado, dale al Badge una `key` que incluya el
  valor del estado.
- **One-time secrets**: `OneTimeSecret` + `CopyButton` (patrón existente).
- **Errores**: `ErrorState` con el mensaje real del API + botón Retry; 403
  muestra "You don't have permission to view this".
- **Copy en inglés** (producto enterprise), comentarios en español.

## Checklist de revisión (aplica a toda PR/tarea UI)

- [ ] Cero SVG caseros / glifos de texto — solo lucide-react con stroke 1.75.
- [ ] Todo botón/input/tabla tiene transición y focus visible.
- [ ] Carga con skeletons sin CLS; errores con `ErrorState` + Retry.
- [ ] Diálogos con entrada suave, Esc cierra, Enter confirma en destructivos.
- [ ] Animaciones solo con las clases `ta-*` existentes, bajo
      `prefers-reduced-motion: no-preference`.
- [ ] `npm run build` + `npm run lint` limpios en la app tocada.
- [ ] Smoke del console (`console/scripts/smoke_authorized.py`) PASS si tocaste
      el console.
