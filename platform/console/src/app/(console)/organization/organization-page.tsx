"use client";

/**
 * Organization — identidad de la organización.
 * Las políticas NO se administran aquí: existe un único panel "Policies"
 * donde se asignan a un departamento o a una persona.
 */
import { Badge, Card, CardBody, CardHeader } from "@/components/ui";
import type { WhoAmI } from "@/lib/types";

export function OrganizationPage({ me }: { me: WhoAmI }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Organization identity"
          description="Resolved from your verified session (never from client input)."
        />
        <CardBody>
          <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted">Organization code</dt>
              <dd className="mt-0.5 text-sm font-medium text-foreground">{me.tenant_slug}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Tenant ID</dt>
              <dd className="mt-0.5 font-mono text-xs text-foreground">{me.tenant_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Your roles</dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {me.roles.map((r) => (
                  <Badge key={`${r.role_key}-${r.department_id ?? "org"}`} tone="accent">
                    {r.role_key}
                    {r.department_id ? " (dept)" : ""}
                  </Badge>
                ))}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Session</dt>
              <dd className="mt-0.5 text-sm text-foreground">
                MFA {me.mfa_verified ? "verified" : "not verified"} · via {me.via}
              </dd>
            </div>
          </dl>
        </CardBody>
      </Card>
    </div>
  );
}
