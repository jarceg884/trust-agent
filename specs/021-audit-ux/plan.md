# Spec 021 — Plan técnico (Audit UX)

| Campo | Valor |
|---|---|
| **Estado** | Aprobado |
| **Versión** | 1.0.0 |
| **Fecha** | 2026-10-26 |

## 1. Contexto de implementación

- Página actual: `platform/console/src/app/(console)/audit/audit-page.tsx` (una
  tarjeta con tabla + diálogo de verificación). Ya usa `useApi`, `api`,
  `downloadBlob`, tipos `AuditQueryResult`/`ChainVerifyResult` — todo correcto y
  se conserva.
- API disponible (spec 008, sin cambios): `GET /api/audit/events` (filtros
  `actor_id`, `action`, `target_type`, `target_id`, `device_id`, `department_id`,
  `since`, `until`, `q`; `limit` ≤ 200, `offset`), `GET /api/audit/events/export`,
  `POST /api/admin/audit/verify` (org_admin).
- El principal se resuelve client-side con `GET /api/me` (vía proxy) para saber si
  el rol `org_admin` aplica al pill de integridad (D-21-02). El layout ya lo
  resolvió server-side para el gate, pero el componente cliente no lo recibe; una
  llamada extra es el camino más simple y honesto (sin estado global nuevo).

## 2. Componentes

### 2.1 `audit-integrity.tsx` (nuevo)

Props: `isAdmin: boolean`, `onVerified: () => void` (refetch del log).

Estado local: `"idle" | "verifying" | "ok" | "broken" | "forbidden" | "error"`.

- `useEffect` en mount: si `isAdmin` y no hay guard en `sessionStorage`
  (`ta_audit_verified_at`), llama `POST /api/admin/audit/verify` vía `api()`.
  200 → `ok`/`broken` + guard + `onVerified()` (el log acaba de recibir
  `audit.verified`). 403 → `forbidden`. Otro error → `error` (con retry manual).
- `verifyNow()`: siempre disponible (sin guard), mismo flujo.
- Render: pill con `Badge` + texto; verde (`Chain intact` + "Every event links to
  the previous one by hash"), rojo (`Chain broken at seq N` + "Investigate
  immediately"), neutral (sin verificar / requiere rol / error con botón Retry).
  Botón "Verify chain" (IconShield) al lado — reemplaza al que abría el diálogo.

### 2.2 `audit-timeline.tsx` (nuevo)

Props: `events: AuditEvent[]` (ya filtrados por la query de la página),
`loading: boolean`, `error: ApiError | null`, `onRetry`, `onPickActor(actorId)`.

- Derivación (`useMemo`, sin estado): orden ascendente por `ts` (la API devuelve
  DESC); agrupación por actor (`actor_id ?? "system"` con `actor_role`); dentro de
  cada actor, split en sesiones en cada `auth.login` (la sesión anterior cierra en
  `auth.logout` o al abrirse la siguiente); etiquetas "Session opened by login" /
  "Earlier activity".
- Tono de badge por acción (función `actionTone`): prefijos `auth.` → info;
  contiene `denied|cutoff|failed|revoked|suspended` → danger; `budget.|policy.` →
  warning; `audit.verified` → success; resto → neutral.
- Fila de nodo: hora (`HH:mm:ss`), badge, target `type:shortId`, payload resumido
  (hasta 3 pares `k=v` acotados a ~40 chars), coste USD, hash corto con `title`
  completo. Rail: `border-l` + nodo (`absolute` círculo) — Tailwind puro.
- Skeleton mientras carga; `EmptyState`/`ErrorState` coherentes con la tabla.

### 2.3 `audit-page.tsx` (rework)

- Cabecera de la `Card`: `AuditIntegrity` (pill + botón) a la izquierda del
  `CardHeader.actions`; export CSV/JSON con el `total` del rango
  (`Export CSV · N`).
- Filtros (grid responsive): Actor (Input + `datalist` de actores vistos),
  Action (Input + `datalist`), Resource type (Select: Any + tipos vistos),
  Resource ID (Input), Device (Input), From/To (date), Free text (Input).
  Apply/Reset/Refresh como hoy. `applied` pasa a llevar TODOS los params.
- Facets: `useApi` extra con `limit=200` SIN filtros (alcance del principal) →
  `useMemo` agrega `actors` (id+role únicos), `actions`, `targetTypes` reales.
- Pestañas Table | Timeline (`role="tablist"`): la tabla es la actual (con botón
  "View activity" por fila de actor → `setActorId(actor)` + apply + tab timeline);
  la timeline recibe los items de la MISMA query (en timeline se fuerza
  `limit=200` y `offset=0` para maximizar la ventana).
- `Pagination` solo en tabla (la timeline muestra hasta 200 con aviso de ventana).
- El diálogo de verificación desaparece (el pill lo sustituye); el toast de error
  de verify se mantiene.

## 3. Datos y tipos

Sin cambios en `types.ts` (`AuditEvent` ya trae todos los campos necesarios,
incl. `payload`, `hash`, `hash_prev`, `device_id`).

## 4. Verificación (convergencia)

1. `npm run build` + `npm run lint` limpios en `platform/console/`.
2. Rebuild del contenedor `trust-agent-console` (corre `next start` de producción).
3. Evidencia real (docs/audit-ux-evidencia.md): sesión admin real (PKCE S256 +
   TOTP RFC 6238 contra Keycloak 26 vivo, tenant nuevo provisionado por el
   proveedor real), flujo de gobernanza real desde la UI/API (departamento,
   presupuesto, usuario), y sobre ese flujo: filtros AC-21-001/002, timeline
   AC-21-003, pill de integridad AC-21-004, export AC-21-005 — todo desde el
   navegador real contra :3100, con verificación cruzada por API directa.

## 5. Riesgos

- **Ruido de `audit.verified`**: mitigado por el guard 1×/sesión (D-21-02).
- **Facets incompletos** (solo últimos 200): documentado en la spec; entrada libre
  de texto cubre el caso general.
- **Sesiones heurísticas**: documentado (D-21-04); el requisito es "qué hizo un
  usuario en orden", que la timeline cumple con orden + agrupación.
