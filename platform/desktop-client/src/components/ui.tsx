/** Componentes base del design system del desktop (dark, mockup-style). */
import type { ReactNode } from "react";

export function Card({
  title,
  icon,
  action,
  children,
  className = "",
}: {
  title?: string;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-border-subtle bg-panel p-5 shadow-sm ${className}`}
    >
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold tracking-wide text-foreground">
            {icon}
            {title}
          </h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

type Tone = "neutral" | "success" | "warning" | "danger" | "accent";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-panel-hover text-muted border-border-subtle",
  success: "bg-[#122117] text-[#3fb972] border-[#1e3428]",
  warning: "bg-[#221a0e] text-[#e0a52e] border-[#3a2c14]",
  danger: "bg-accent-soft text-[#ff5a6a] border-[#3a1a1f]",
  accent: "bg-accent-soft text-[#ff5a6a] border-[#3a1a1f]",
};

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${toneClasses[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatusDot({ tone }: { tone: Tone }) {
  const colors: Record<Tone, string> = {
    neutral: "bg-muted",
    success: "bg-[#3fb972]",
    warning: "bg-[#e0a52e]",
    danger: "bg-[#ff5a6a]",
    accent: "bg-accent",
  };
  return (
    <span
      aria-hidden
      className={`inline-block h-1.5 w-1.5 rounded-full ${colors[tone]}`}
    />
  );
}

export function ProgressBar({
  spentCents,
  budgetCents,
  cutoff,
}: {
  spentCents: number;
  budgetCents: number | null;
  cutoff: boolean;
}) {
  if (budgetCents === null || budgetCents <= 0) {
    return (
      <div className="h-1.5 w-full rounded-full bg-panel-hover" aria-label="No budget set" />
    );
  }
  const pct = Math.min(100, Math.round((spentCents / budgetCents) * 100));
  const color = cutoff
    ? "bg-[#ff5a6a]"
    : pct >= 80
      ? "bg-[#e0a52e]"
      : "bg-accent";
  return (
    <div
      className="h-1.5 w-full rounded-full bg-panel-hover"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Budget used"
    >
      <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border-subtle px-4 py-6 text-center text-sm text-muted">
      {children}
    </p>
  );
}

export function ErrorState({
  message,
  requestId,
}: {
  message: string;
  requestId?: string | null;
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-[#3a1a1f] bg-accent-soft px-4 py-3 text-sm"
    >
      <p className="font-medium text-[#ff8a95]">{message}</p>
      {requestId && (
        <p className="mt-1 font-mono text-xs text-muted">request_id: {requestId}</p>
      )}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-panel-hover ${className}`}
      aria-hidden
    />
  );
}

/** Etiqueta de nivel de presupuesto (user/dept/org). */
export function LevelTag({ level }: { level: string }) {
  const labels: Record<string, string> = {
    user: "You",
    dept: "Department",
    org: "Organization",
  };
  return (
    <span className="text-xs font-medium uppercase tracking-wider text-muted">
      {labels[level] ?? level}
    </span>
  );
}
