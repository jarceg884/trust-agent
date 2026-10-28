"use client";

/**
 * Selector de conexiones permitidas (spec 024 REQ-024-012b): checkbox list
 * con los TRES tipos de token de la policy key `allowed_connections`:
 *  - "*"  → todo el catálogo de conectores (sujeto a allowed_connectors);
 *  - slug → un conector del catálogo fijo;
 *  - id   → una conexión personalizada ACTIVA del tenant (UUID).
 * Se usa en Organization (org), Departments (depto) y Users (usuario);
 * el control plane valida cada token (422 si es desconocido, D-24-07).
 */
import { useApi } from "@/hooks/use-api";
import type { ConnectorList, CustomConnectionList } from "@/lib/types";
import { Badge, ErrorState } from "@/components/ui";

export const CONNECTIONS_POLICY_KEY = "allowed_connections";

export function AllowedConnectionsSelector({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const catalog = useApi<ConnectorList>("/api/connectors");
  const custom = useApi<CustomConnectionList>("/api/vault/connections");

  if (catalog.error || custom.error) {
    return (
      <ErrorState
        error={catalog.error ?? custom.error!}
        context="GET /api/connectors, GET /api/vault/connections"
      />
    );
  }
  if (catalog.loading || custom.loading) {
    return <p className="py-3 text-xs text-muted">Loading connections…</p>;
  }

  const tokens = new Set(value);
  const toggle = (token: string) => {
    const next = new Set(tokens);
    if (next.has(token)) next.delete(token);
    else next.add(token);
    onChange([...next]);
  };

  const row = (
    token: string,
    label: string,
    hint: string,
    tone: "accent" | "info" | "success" = "info",
    disabled = false,
  ) => (
    <label
      key={token}
      className={`flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-border-subtle px-3 py-2.5 ${
        disabled ? "cursor-not-allowed opacity-50" : "hover:bg-surface-alt/50"
      }`}
    >
      <span className="flex items-center gap-2.5">
        <input
          type="checkbox"
          className="h-4 w-4 accent-[var(--accent)]"
          checked={tokens.has(token)}
          disabled={disabled}
          onChange={() => toggle(token)}
        />
        <span>
          <span className="block text-sm font-medium text-foreground">{label}</span>
          <span className="block font-mono text-[10px] text-muted">{hint}</span>
        </span>
      </span>
      <Badge tone={tone}>{token === "*" ? "wildcard" : tone === "success" ? "custom" : "catalog"}</Badge>
    </label>
  );

  const customActive = (custom.data?.connections ?? []).filter(
    (c) => c.status === "active",
  );

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <p className="text-xs font-semibold text-foreground">Catalog connectors</p>
        {row("*", "Whole catalog", "* — every catalog connector (still gated by allowed_connectors)", "accent")}
        {(catalog.data?.connectors ?? []).map((c) => row(c.slug, c.name, c.slug))}
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-semibold text-foreground">
          Custom connections {customActive.length === 0 ? "(none defined)" : ""}
        </p>
        {customActive.length > 0 ? (
          customActive.map((c) =>
            row(c.id, c.name, `${c.kind} · ${c.id}`, "success"),
          )
        ) : (
          <p className="rounded-lg border border-dashed border-border-subtle px-3 py-2.5 text-[11px] text-muted">
            Custom connections are created by the organization admin in
            Credentials → Custom connections. They are only usable when their
            id is explicitly selected here.
          </p>
        )}
      </div>
      <p className="text-[11px] leading-relaxed text-muted">
        Deny-overrides: each level can only <em>tighten</em> the level above.
        Saving re-runs the policy sweep — active connector instances that the
        new effective policy no longer allows are cut off immediately.
      </p>
    </div>
  );
}
