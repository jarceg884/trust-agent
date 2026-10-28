# Evidencia — Budgets: edición del budget de la organización visible y auto-incrementable (t_aa139606)

**Repro de Miller**: "tampoco veo una opción de incrementar el budget de la corporación. Aunque eso el admin debería poder auto incrementarse."

**Fecha**: 2026-10-28 · **Perfil**: ui · **Todo real, nada simulado** (constitution I).

## Qué cambió

`platform/console/src/app/(console)/budgets/budgets-page.tsx`:

1. **Card destacada "Organization budget" de primera intención** (primera card
   de la página, estilo accent `border-accent-border bg-accent-soft/40`):
   límite mensual en cifra grande, usado con %, barra de progreso, badge de
   cutoff, resumen de asignación (Σ deptos asignado · sin asignar) y botón
   **Edit budget** (variante primary) en el header. Antes el nivel org era
   solo la primera fila de la jerarquía — el admin no lo encontraba.
2. El botón **Edit budget** está gateado por el permiso real
   `budget.org.manage` (data-driven desde `GET /api/me`, patrón D-17-01);
   sin el permiso muestra "Requires budget.org.manage" en lugar de un botón
   que fallaría con 403.
3. La jerarquía (deptos→usuarios) ya no duplica la fila org.
4. Copy del diálogo de edición org: "Raising the organization budget is
   always allowed; lowering it below the spend already recorded asks for
   confirmation." — describe la validación real del control plane.

## Confirmación de que el org_admin puede auto-incrementar (sin gate escondido)

- **Permiso**: `PUT /api/budgets/org` exige únicamente `budget.org.manage`
  (`app/governance/router.py:48-58`), y el rol `org_admin` lo tiene por
  semilla (`db/schema.sql:99`). No interviene el provider.
- **Validación**: `set_org_budget` (`app/governance/budgets.py:192-209`) NO
  tiene restricción de suma — la Σ deptos ≤ org se valida al editar
  *deptos* contra el techo org, por lo que **subir el org siempre es
  válido**. La única validación extra es `force-below-spend` (REQ-BUD-013),
  que solo aplica al BAJAR por debajo del gasto del periodo.
- **Empírico**: el flujo E2E below subió $3,500 → $5,000 desde la UI con el
  propio org_admin, sin force y sin aprobación del provider.

## Flujo E2E real (browser contra stack vivo)

Login real PKCE S256 + one-time password + enrolamiento TOTP + step-up MFA
(reutiliza el baile de `t_6d3f5016`), sesión admin del tenant
`console-smoke2` inyectada en el navegador. Console = imagen Docker
reconstruida (`docker compose build console`) tras `npm run build` limpio.

| Paso | Captura | Verificación |
|------|---------|--------------|
| 1. `/budgets` — card destacada primera, botón Edit budget visible, límite $3,500.00 | `01-budgets-page-org-card-before.png` | DOM: `Organization budget` aparece antes que `Budget hierarchy`; `Edit budget` presente |
| 2. Click **Edit budget** → diálogo con monto prellenado, se escribe `5000` | `02-edit-org-budget-dialog.png` | DOM: diálogo abierto, input=5000 |
| 3. **Save budget** → toast "Organization budget updated.", card refleja **$5,000.00** | `03-org-budget-raised-reflected.png` | DOM: `$5,000.00` visible, diálogo cerrado |

### Verificación server-side del mismo flujo

```
GET /api/proxy/budgets/overview → organization.budget_cents = 500000
DB (tenant console-smoke2): budgets level=org → amount_cents=500000, status=active
audit_log: action=budget.updated, payload.amount_cents=500000, ts=2026-10-28 19:58:29 UTC
```

## Cómo verificarlo

```bash
cd /root/trust-agent && docker compose up -d   # stack vivo
# login como org_admin en http://127.0.0.1:3100 → Budgets:
# la primera card es "Organization budget" con botón "Edit budget".
# Subir el monto → toast + cifra actualizada; verificar en DB:
docker exec trust-agent-db psql -U postgres -d trust_agent -t \
  -c "SELECT amount_cents FROM budgets b JOIN tenants t ON t.id=b.tenant_id \
      WHERE t.slug='<slug>' AND b.level='org';"
```

Build: `npm run build` en `platform/console` compila limpio (verificado
antes de reconstruir la imagen).
