---
name: nextjs-dynamic-branding-titles
description: Use when a Next.js App Router app needs per-tenant page titles or shell chrome that follows remote branding without reload. Proven pattern from trust-agent spec 028 (t_42e23b01).
category: software-development
---

# Next.js: títulos de página por tenant + refresh del shell sin reload

Patrón verificado en el Admin Console de trust-agent (spec 028 D-028-08/09,
REQ-BRD2-007/008). Problema: `<title>` estático pese a branding white-label, y
sidebar/favicon que solo refrescan tras reload.

## Títulos dinámicos (server-side)

1. `generateMetadata()` en el **layout** del segmento autenticado (no en cada
   página): devuelve `title: { default: "{name} — Admin Console", template: "%s — {name}" }`.
2. Las páginas hijas exportan SOLO su segmento (`title: "Overview"`) — el
   template del layout añade " — {product_name}". Migrar títulos existentes
   es un regex de una línea: `title: "X — Trust Agent Admin Console"` → `title: "X"`.
3. **Compartir el fetch entre `generateMetadata` y el cuerpo del layout**
   (p. ej. el mismo GET sirve título + acento): envolver la llamada en
   `cache()` de React (`export const getPublishedBranding = cache(async (s) => …)`).
   Sin `cache()` son DOS fetch por navegación — generateMetadata y el render
   del layout no comparten closures, pero sí la memoización por request.
4. Páginas pre-auth (login/signup): título estático del proveedor — el tenant
  no se conoce sin sesión. Documentar la razón en la spec (constitution II).

## Refresh del shell SIN reload tras publicar branding

- Página de administración: tras la mutación exitosa (publish/rollback) →
  `window.dispatchEvent(new CustomEvent("trust:branding-updated"))` +
  `router.refresh()` (este último re-renderiza el RSC → el CSS server-side
  del layout, p. ej. el acento, se actualiza).
- Componente del shell (sidebar brand): `useEffect(() =>
  onBrandingUpdated(() => branding.refetch()), [branding.refetch])` —
  `refetch` de un hook propio con `useCallback` es estable; NO pasar el
  objeto del hook entero como dep (cambia identidad cada render y
  re-suscribe infinito).
- `<title>` live: efecto que reemplaza el segmento final tras " — "
  (`document.title.split(" — ")` → último segmento = product_name). Es
  idempotente en el mount (mismo nombre → mismo título) y cubre el cambio
  sin navegación.

## Pitfalls

- `useApi`-style hooks sin cache: el shell NO ve el publish de otra página
  — por eso el evento; el refetch explícito tras mutación (constitution I)
  solo refresca los hooks de la página que mutó.
- Verificar "sin reload" con un marcador: `window.__marker = 42` antes del
  clic; si sobrevive, no hubo reload. Evidencia DOM (document.title,
  innerText del sidebar, href del link[rel=icon], getComputedStyle del
  acento) es más fuerte que un screenshot.
- El contenedor Docker sirve la imagen build-eada: `npm run build` local NO
  basta — `docker compose build console && docker compose up -d console`.
