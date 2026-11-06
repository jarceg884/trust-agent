# Plan 037 — UX Shared Agents + Credentials

## Arquitectura del cambio

Capa de presentación (2 páginas del console) + 2 campos aditivos de solo-lectura
en el control plane. Cero mutaciones nuevas, cero cambios de semántica de
gobernanza.

```
platform/app/connectors/router.py    GET /api/connectors  + principal → allowed_for_caller
platform/app/connectors/service.py   helper policy_state_for_catalog(conn, principal)
platform/app/vault/connections.py    list_connections → allowed_for_caller por fila
platform/console/src/lib/types.ts    + allowed_for_caller en ConnectorCatalogEntry/CustomConnectionMeta
platform/console/src/components/ui.tsx  + Tabs (componente compartido)
platform/console/src/app/(console)/agents/agents-page.tsx      REDISEÑO (pestañas + tarjetas + user picker + grants list)
platform/console/src/app/(console)/credentials/credentials-page.tsx  REDISEÑO (secciones + wizard + badges)
platform/tests/test_catalogs.py          + assert allowed_for_caller
platform/tests/test_custom_connections.py + assert allowed_for_caller
```

## Pasos

1. **Backend**: campo `allowed_for_caller`.
   - `connectors/service.py`: nueva función `catalog_with_policy(conn, principal)`
     que devuelve el catálogo COMPLETO (no filtrado) con el bool por entrada
     (regla `connection_tokens_allow` + `allowed_connectors`).
   - `connectors/router.py`: `GET /api/connectors` toma `get_principal`.
   - `vault/connections.py`: `list_connections` añade `allowed_for_caller`
     (`id ∈ allowed_connection_tokens`).
   - Tests: asserts nuevos en test_catalogs.py y test_custom_connections.py.
2. **Frontend tipos**: `allowed_for_caller` en los dos tipos.
3. **Componente Tabs** en `ui.tsx` (accesible: role=tablist/tab/tabpanel,
   teclado ←/→, aria-selected; estilo del design system).
4. **agents-page.tsx** (rediseño completo, D-37-01/02/07):
   - Pestañas + contadores; default "Default profiles" si no hay agentes.
   - Strip de policy efectiva inline (modelos aprobados + budget badge).
   - Grid de tarjetas de perfil (ta-rise) con Adopt.
   - Tabla "My agents": dueño por nombre (`GET /api/users`), acceso, grants
     (fetch por agente administrable), acciones Invoke / Share / Manage access.
   - Dialog Share: selector de usuario filtrable (input + lista) + permiso.
   - Dialog Manage access: lista de grants + Revoke por fila.
   - Dialog Invoke: modelo preseleccionado del set aprobado + veredicto llano.
5. **credentials-page.tsx** (rediseño completo, D-37-04/05/06):
   - Sección "Catalog connections": grid de tarjetas con badge de policy y
     Connect deshabilitado si bloqueado.
   - Sección "My custom connections": tabla con badge de policy + wizard.
   - Sección "My secrets": vault personal con labels llanos.
   - Wizard 3 pasos con indicador de progreso y one-time secret final.
6. **Build local**: `npm run build` + `npm run lint` limpios.
7. **Deploy**: `docker build` de platform y console + `up -d --no-deps` de
   `api` y `console` (patrón skill nextjs-console-patterns §4).
8. **E2E evidencia**: sesión mintada (smoke_console) → browser CDP → flujo
   adopt + wizard → screenshots + OCR.

## Riesgos

- `GET /api/connectors` pasa a requerir principal: ya está detrás de
  `require_mfa` (router dependencies), todos los callers autenticados — el
  test de 401 sin sesión sigue igual.
- El fetch de grants por agente administrable: N agentes propios → N GETs
  pequeños; aceptable en escala de consola (los grants solo se piden para
  agentes own/manage; los ajenos no muestran contador).
- Sesión del browser caduca ~30 min → ráfagas cortas con re-mint (skill §13).
