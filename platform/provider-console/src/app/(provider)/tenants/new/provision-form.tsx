"use client";

/**
 * Formulario de provisioning (spec 014 REQ-PC-012, AC-PC-012).
 *
 * Llama al POST /api/admin/tenants real vía el proxy BFF. La respuesta 201
 * contiene `one_time_activation` (password temporal del admin inicial) que se
 * renderiza UNA sola vez en el panel one-time (D-PC-06): vive SOLO en React
 * state — sin localStorage/sessionStorage/DB — y se pierde al navegar.
 * Errores del API (422/503/…) se muestran con su reason real.
 */
import { useState } from "react";
import Link from "next/link";
import { ApiError, api } from "@/lib/api-client";
import type { ProvisionResult } from "@/lib/types";
import { isValidSlug } from "@/lib/config";
import { usd } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CopyButton,
  Field,
  Input,
  OneTimeSecret,
  useToast,
} from "@/components/ui";
import { IconCheck } from "@/components/icons";

const DEPARTMENT_TEMPLATES = [
  { key: "it_operations", label: "IT Operations" },
  { key: "cloud_operations", label: "Cloud Operations" },
  { key: "finance", label: "Finance" },
  { key: "customer_operations", label: "Customer Operations" },
] as const;

export function ProvisionForm() {
  const { push } = useToast();
  const [orgName, setOrgName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [budgetUsd, setBudgetUsd] = useState("500");
  const [modelPlan, setModelPlan] = useState<"standard" | "custom">("standard");
  const [customModels, setCustomModels] = useState("");
  const [departments, setDepartments] = useState<string[]>(
    DEPARTMENT_TEMPLATES.map((d) => d.key),
  );
  const [slug, setSlug] = useState("");
  const [productName, setProductName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{
    status: number;
    reason: string;
    message: string;
  } | null>(null);
  // Resultado del provisioning: el OTA vive SOLO aquí (D-PC-06).
  const [result, setResult] = useState<ProvisionResult | null>(null);

  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(adminEmail.trim());
  const budgetValid = /^\d+(\.\d{1,2})?$/.test(budgetUsd.trim());
  const slugValid = slug.trim() === "" || isValidSlug(slug.trim());
  const modelsValid =
    modelPlan === "standard" ||
    customModels.trim().split(/[\s,]+/).filter(Boolean).length > 0;
  const canSubmit =
    orgName.trim().length >= 2 &&
    emailValid &&
    budgetValid &&
    slugValid &&
    modelsValid &&
    departments.length > 0 &&
    !loading;

  function toggleDepartment(key: string) {
    setDepartments((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        organization_name: orgName.trim(),
        admin_email: adminEmail.trim(),
        initial_budget_usd: parseFloat(budgetUsd.trim()),
        model_plan:
          modelPlan === "standard"
            ? "standard"
            : customModels.trim().split(/[\s,]+/).filter(Boolean),
        departments,
      };
      if (slug.trim()) body.slug = slug.trim();
      if (productName.trim()) {
        body.branding = { product_name: productName.trim() };
      }
      const res = await api<ProvisionResult>("/api/admin/tenants", {
        method: "POST",
        body,
      });
      setResult(res);
      push(
        "success",
        res.created
          ? `Tenant "${res.tenant.name}" provisioned.`
          : `Tenant "${res.tenant.name}" already existed (idempotent re-run).`,
      );
    } catch (cause) {
      if (cause instanceof ApiError) {
        setError({
          status: cause.status,
          reason: cause.reason,
          message: cause.message,
        });
      } else {
        setError({
          status: 500,
          reason: "unknown",
          message: "Unexpected error during provisioning.",
        });
      }
    } finally {
      setLoading(false);
    }
  }

  // ------- Resultado: card del tenant + panel OTA one-time -------
  if (result) {
    const ota = result.one_time_activation ?? undefined;
    const prov = result.provisioned;
    return (
      <div className="space-y-6">
        <Card>
          <CardHeader
            title={
              <span className="flex items-center gap-2">
                <IconCheck size={15} className="text-success" />
                {result.created ? "Tenant provisioned" : "Tenant already exists"}
              </span>
            }
            description="The organization is live on the platform."
            actions={
              <Link href={`/tenants/${result.tenant.slug}`}>
                <Button variant="secondary" size="sm">
                  View tenant →
                </Button>
              </Link>
            }
          />
          <CardBody>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm md:grid-cols-3">
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Organization
                </dt>
                <dd className="mt-1 font-medium">{result.tenant.name}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Slug / Realm
                </dt>
                <dd className="mt-1 font-mono text-xs">
                  {result.tenant.slug} / {result.tenant.oidc_realm}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Status
                </dt>
                <dd className="mt-1">
                  <Badge tone="success">{result.tenant.status}</Badge>
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Initial admin
                </dt>
                <dd className="mt-1">{result.admin?.email ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Org budget
                </dt>
                <dd className="mt-1">
                  {prov ? `${usd(prov.budget_cents)} / month` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Healthcheck
                </dt>
                <dd className="mt-1">
                  <Badge
                    tone={result.healthcheck.status === "ok" ? "success" : "warning"}
                  >
                    {result.healthcheck.status}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Departments
                </dt>
                <dd className="mt-1 text-xs">
                  {prov ? prov.departments.map((d) => d.name).join(", ") : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Approved models
                </dt>
                <dd className="mt-1 text-xs">
                  {prov ? `${prov.models.length} model(s)` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wider text-faint">
                  Default policies
                </dt>
                <dd className="mt-1 text-xs">
                  {prov ? `${prov.policies.length} active` : "—"}
                </dd>
              </div>
            </dl>
          </CardBody>
        </Card>

        {ota ? (
          <Card>
            <CardHeader
              title="One-time activation — initial admin credentials"
              description="Deliver these credentials to the customer's admin now."
            />
            <CardBody className="space-y-4">
              <p className="rounded-lg border border-warning/25 bg-warning-soft px-3.5 py-2.5 text-xs leading-relaxed text-warning">
                <strong>Shown only once — copy it now.</strong> This temporary
                password is never stored and cannot be retrieved again. The
                first sign-in forces a password change. TOTP (MFA) enrollment
                is enforced only when the organization&apos;s policy requires
                MFA (<code>require_mfa=true</code>); otherwise the Admin
                Console suggests it via the &ldquo;Set up MFA&rdquo; banner.
              </p>
              <dl className="space-y-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <dt className="text-xs uppercase tracking-wider text-faint">
                    Username
                  </dt>
                  <dd className="font-mono text-xs">{ota.username}</dd>
                </div>
                <div>
                  <dt className="mb-1.5 text-xs uppercase tracking-wider text-faint">
                    One-time password
                  </dt>
                  <dd>
                    <OneTimeSecret value={ota.one_time_password} />
                  </dd>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <dt className="text-xs uppercase tracking-wider text-faint">
                    Issuer
                  </dt>
                  <dd className="font-mono text-xs break-all">{ota.issuer_url}</dd>
                  <CopyButton value={ota.issuer_url} label="Copy issuer" />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <dt className="text-xs uppercase tracking-wider text-faint">
                    Client ID
                  </dt>
                  <dd className="font-mono text-xs">{ota.client_id}</dd>
                </div>
              </dl>
              <p className="text-xs leading-relaxed text-muted">
                {ota.instructions}
              </p>
            </CardBody>
          </Card>
        ) : (
          <p className="rounded-lg border border-border-subtle bg-surface-alt px-4 py-3 text-sm text-muted">
            No credentials returned: this slug already existed (idempotent
            re-run). One-time credentials are delivered only on first creation.
          </p>
        )}

        <div className="flex gap-2">
          <Link href="/tenants">
            <Button variant="secondary" size="sm">
              ← Back to tenants
            </Button>
          </Link>
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              setResult(null);
              setOrgName("");
              setAdminEmail("");
              setBudgetUsd("500");
              setSlug("");
              setProductName("");
              setDepartments(DEPARTMENT_TEMPLATES.map((d) => d.key));
            }}
          >
            Provision another client
          </Button>
        </div>
      </div>
    );
  }

  // ------- Formulario -------
  return (
    <Card>
      <CardHeader
        title="New customer organization"
        description="All fields are validated by the control plane; the operation is atomic (full rollback on failure)."
      />
      <CardBody>
        <form onSubmit={submit} className="space-y-5" noValidate>
          {error ? (
            <p
              role="alert"
              className="rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger"
            >
              {error.message}
              <span className="mt-1 block font-mono text-[10px] opacity-75">
                {error.reason} · HTTP {error.status}
              </span>
            </p>
          ) : null}

          <div className="grid gap-5 md:grid-cols-2">
            <Field
              label="Organization name"
              htmlFor="orgName"
              hint="Display name of the customer (e.g. Globex Industries)."
            >
              <Input
                id="orgName"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Globex Industries"
                required
              />
            </Field>
            <Field
              label="Initial admin email"
              htmlFor="adminEmail"
              hint="The first org admin; receives one-time credentials."
            >
              <Input
                id="adminEmail"
                type="email"
                value={adminEmail}
                onChange={(e) => setAdminEmail(e.target.value)}
                placeholder="admin@globex.com"
                required
              />
            </Field>
            <Field
              label="Monthly org budget (USD)"
              htmlFor="budget"
              hint="Organization-level budget for the current period."
              error={budgetUsd && !budgetValid ? "Enter a valid USD amount." : null}
            >
              <Input
                id="budget"
                inputMode="decimal"
                value={budgetUsd}
                onChange={(e) => setBudgetUsd(e.target.value)}
                placeholder="500"
                required
              />
            </Field>
            <Field
              label="Slug (optional)"
              htmlFor="slug"
              hint="Defaults to a slug derived from the name. Also the Keycloak realm."
              error={!slugValid ? "Lowercase letters, numbers and dashes." : null}
            >
              <Input
                id="slug"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                placeholder="globex-industries"
              />
            </Field>
            <Field
              label="Product name (optional)"
              htmlFor="productName"
              hint="White-label branding; defaults to Trust Agent."
            >
              <Input
                id="productName"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                placeholder="Globex AI"
              />
            </Field>
          </div>

          <fieldset>
            <legend className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
              Model plan
            </legend>
            <div className="flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="modelPlan"
                  checked={modelPlan === "standard"}
                  onChange={() => setModelPlan("standard")}
                />
                Standard — all available catalog models
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="radio"
                  name="modelPlan"
                  checked={modelPlan === "custom"}
                  onChange={() => setModelPlan("custom")}
                />
                Custom list
              </label>
            </div>
            {modelPlan === "custom" ? (
              <div className="mt-3">
                <Input
                  aria-label="Custom model names"
                  value={customModels}
                  onChange={(e) => setCustomModels(e.target.value)}
                  placeholder="glm-5.2, deepseek-v4-flash"
                />
                <p className="mt-1 text-xs text-muted">
                  Comma or space separated model names from the global catalog.
                </p>
              </div>
            ) : null}
          </fieldset>

          <fieldset>
            <legend className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
              Seed departments
            </legend>
            <div className="flex flex-wrap gap-2">
              {DEPARTMENT_TEMPLATES.map((d) => {
                const active = departments.includes(d.key);
                return (
                  <button
                    key={d.key}
                    type="button"
                    onClick={() => toggleDepartment(d.key)}
                    aria-pressed={active}
                    className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                      active
                        ? "border-accent-border bg-accent-soft text-accent"
                        : "border-border-subtle bg-surface text-muted hover:bg-surface-alt"
                    }`}
                  >
                    {active ? "✓ " : ""}
                    {d.label}
                  </button>
                );
              })}
            </div>
            <p className="mt-1.5 text-xs text-muted">
              Each department is seeded with its pre-designed Hermes profile
              template.
            </p>
          </fieldset>

          <div className="flex items-center justify-end gap-3 border-t border-border-subtle pt-4">
            <p className="mr-auto text-xs leading-relaxed text-muted">
              Creates a real Keycloak realm, admin user (temporary password;
              MFA enforced only if the org policy requires it), org budget,
              default policies and approved models.
            </p>
            <Button type="submit" variant="primary" loading={loading} disabled={!canSubmit}>
              Provision tenant
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
