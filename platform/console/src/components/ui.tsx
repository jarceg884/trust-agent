"use client";

/**
 * UI kit (spec 009 D-AC-04, REQ-AC-031): primitivas enterprise accesibles.
 * Tema claro del mockup: superficies blancas, bordes sutiles, acento #c90104.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { IconAlert, IconCheck, IconClose, IconCopy } from "./icons";

// --- Button ------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  // Pulido v2 (t_3cf085b9): el primario gana profundidad (sombra sutil +
  // inset superior) para transmitir confianza; el disabled es gris neutro —
  // NUNCA rojo translúcido (a 40% de opacidad se leía como salmón roto).
  primary:
    "bg-accent text-white hover:bg-accent-hover border border-transparent " +
    "shadow-[inset_0_1px_0_rgba(255,255,255,0.10),0_1px_2px_rgba(16,24,40,0.12)] " +
    "hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.10),0_2px_8px_rgba(79,70,229,0.30)] " +
    "disabled:bg-surface-alt disabled:text-faint disabled:shadow-none",
  secondary:
    "bg-surface text-foreground hover:bg-surface-alt border border-border-subtle hover:border-foreground/20 disabled:opacity-50",
  ghost:
    "bg-transparent text-muted hover:text-foreground hover:bg-surface-alt border border-transparent disabled:opacity-50",
  danger:
    "bg-surface text-danger hover:bg-danger-soft border border-danger/30 disabled:opacity-50",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "px-2.5 py-1.5 text-xs gap-1.5",
  md: "px-3.5 py-2 text-sm gap-2",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  children,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={`inline-flex items-center justify-center rounded-lg font-medium transition-all duration-150 ease-out active:scale-[0.98] disabled:cursor-not-allowed disabled:active:scale-100 ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
    >
      {loading ? (
        <span
          aria-hidden="true"
          className="inline-block size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      ) : null}
      {children}
    </button>
  );
}

// --- Card ----------------------------------------------------------------------

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-border-subtle bg-surface shadow-[0_1px_2px_rgba(16,24,40,0.04)] ${className}`}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle px-5 py-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description ? (
          <p className="mt-0.5 text-xs text-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function CardBody({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`px-5 py-4 ${className}`}>{children}</div>;
}

// --- Badge ---------------------------------------------------------------------

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info" | "accent";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-alt text-muted border-border-subtle",
  success: "bg-success-soft text-success border-success/20",
  warning: "bg-warning-soft text-warning border-warning/20",
  danger: "bg-danger-soft text-danger border-danger/20",
  info: "bg-info-soft text-info border-info/20",
  accent: "bg-accent-soft text-accent border-accent-border",
};

export function Badge({
  tone = "neutral",
  children,
  className = "",
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 ta-pop ${BADGE_TONES[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

// --- Inputs ----------------------------------------------------------------------

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-xs font-medium text-foreground"
      >
        {label}
      </label>
      {children}
      {hint && !error ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
      {error ? (
        <p role="alert" className="mt-1 text-xs text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

// Pulido v2 (t_3cf085b9): focus NEUTRO estilo Stripe/Linear — el borde
// oscurece y un ring suave del mismo tono acompaña. El acento rojo en un
// input se reservaba para error; un focus rojo en campo vacío parecía un
// error permanente (hallazgo #1 de la crítica visual).
const INPUT_CLASS =
  "w-full rounded-lg border border-border-subtle bg-surface px-3 py-2 text-sm text-foreground transition-all duration-150 placeholder:text-faint hover:border-foreground/25 focus:border-foreground/40 focus:ring-[3px] focus:ring-foreground/[0.08] focus:outline-none disabled:cursor-not-allowed disabled:bg-surface-alt disabled:text-muted";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${INPUT_CLASS} ${props.className ?? ""}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={`${INPUT_CLASS} ${props.className ?? ""}`} />
  );
}

export function Textarea(
  props: React.TextareaHTMLAttributes<HTMLTextAreaElement>,
) {
  return (
    <textarea
      {...props}
      className={`${INPUT_CLASS} font-mono text-xs ${props.className ?? ""}`}
    />
  );
}

// --- Table ------------------------------------------------------------------------

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  className = "",
}: {
  children?: ReactNode;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={`border-b border-border-subtle bg-surface-alt/60 px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className = "",
  colSpan,
}: {
  children?: ReactNode;
  className?: string;
  colSpan?: number;
}) {
  return (
    <td
      colSpan={colSpan}
      className={`border-b border-border-subtle px-4 py-3 align-middle text-foreground ${className}`}
    >
      {children}
    </td>
  );
}

// --- Skeletons ----------------------------------------------------------------------

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`ta-skeleton ${className}`} />;
}

export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-3 px-5 py-5">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton
              key={c}
              className={`h-4 ${c === 0 ? "w-1/3" : c === cols - 1 ? "w-16" : "w-1/5"}`}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// --- Empty / Error states -------------------------------------------------------------

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      {icon ? (
        <div className="flex size-11 items-center justify-center rounded-full bg-surface-alt text-muted">
          {icon}
        </div>
      ) : null}
      <div>
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description ? (
          <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-muted">
            {description}
          </p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  context,
}: {
  error: { status: number; reason: string; message: string; requestId?: string | null };
  onRetry?: () => void;
  context?: string;
}) {
  const permission =
    error.status === 403 &&
    !["mfa_required", "csrf_required"].includes(error.reason);
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center"
    >
      <div className="flex size-11 items-center justify-center rounded-full bg-danger-soft text-danger">
        <IconAlert size={20} />
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground">
          {permission ? "You don't have permission to view this" : "Something went wrong"}
        </p>
        <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-muted">
          {error.message}
        </p>
        <p className="mt-2 font-mono text-[10px] text-faint">
          {context ? `${context} · ` : ""}
          {error.reason}
          {error.requestId ? ` · request ${error.requestId}` : ""}
        </p>
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

// --- Dialog ----------------------------------------------------------------------------

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children?: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // Focus inicial dentro del diálogo.
    const t = setTimeout(() => ref.current?.focus(), 0);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      clearTimeout(t);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="ta-dialog-backdrop fixed inset-0 z-50 flex items-center justify-center bg-foreground/35 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`ta-dialog-panel w-full ${width} rounded-xl border border-border-subtle bg-surface shadow-xl focus:outline-none`}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border-subtle px-5 py-4">
          <div>
            <h2 id={titleId} className="text-sm font-semibold text-foreground">
              {title}
            </h2>
            {description ? (
              <p className="mt-0.5 text-xs text-muted">{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-md p-1 text-muted transition-colors hover:bg-surface-alt hover:text-foreground"
          >
            <IconClose size={16} />
          </button>
        </header>
        <div className="px-5 py-4">{children}</div>
        {footer ? (
          <footer className="flex justify-end gap-2 border-t border-border-subtle px-5 py-3.5">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}

// --- Confirm (destructivo) -----------------------------------------------------------

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  loading,
  title,
  message,
  confirmLabel = "Confirm",
  danger = true,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  loading?: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
}) {
  // Atajo de teclado (estándar pro): Enter confirma salvo cuando el foco está
  // en un botón (el Enter nativo de ese botón manda — p. ej. Cancel).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || loading) return;
      const el = document.activeElement;
      if (el instanceof HTMLButtonElement) return;
      e.preventDefault();
      onConfirm();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, loading, onConfirm]);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      width="max-w-md"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            size="sm"
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm leading-relaxed text-muted">{message}</p>
    </Dialog>
  );
}

// --- Copy + one-time secret -----------------------------------------------------------

export function CopyButton({
  value,
  label,
  variant = "secondary",
}: {
  value: string;
  label?: string;
  /** Pulido v2 r3: en Hermes Download "Copy link" es la acción PRIMARIA
   *  de la página (crítica visual: jerarquía de acciones). */
  variant?: "primary" | "secondary" | "ghost";
}) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant={variant}
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
        } catch {
          // fallback: selección manual
          const ta = document.createElement("textarea");
          ta.value = value;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
    >
      {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
      {copied ? "Copied" : (label ?? "Copy")}
    </Button>
  );
}

/** Secreto one-time (constitution IV): se muestra EXACTAMENTE una vez. */
export function OneTimeSecret({ value }: { value: string }) {
  return (
    <div className="space-y-3">
      <p className="rounded-lg border border-warning/25 bg-warning-soft px-3.5 py-2.5 text-xs leading-relaxed text-warning">
        This value is shown <strong>only once</strong>. Store it in a secure
        location — it can never be retrieved again.
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-border-subtle bg-surface-alt px-3 py-2.5 font-mono text-xs text-foreground">
          {value}
        </code>
        <CopyButton value={value} />
      </div>
    </div>
  );
}

// --- Progress ----------------------------------------------------------------------------

export function Progress({ pct, tone = "accent" }: { pct: number; tone?: "accent" | "warning" | "danger" }) {
  const color =
    tone === "danger"
      ? "bg-danger"
      : tone === "warning"
        ? "bg-warning"
        : "bg-accent";
  return (
    <div
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-alt"
    >
      <div className={`h-full rounded-full transition-[width] duration-500 ease-out ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

// --- Pagination ----------------------------------------------------------------------------

export function Pagination({
  offset,
  limit,
  total,
  onPageChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onPageChange: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <nav
      aria-label="Pagination"
      className="flex items-center justify-between border-t border-border-subtle px-5 py-3"
    >
      <p className="text-xs text-muted">
        {total === 0
          ? "No results"
          : `${offset + 1}–${Math.min(offset + limit, total)} of ${total}`}
      </p>
      <div className="flex items-center gap-1.5">
        <Button
          variant="secondary"
          size="sm"
          disabled={offset === 0}
          onClick={() => onPageChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <span className="px-1 text-xs text-muted">
          Page {page} of {pages}
        </span>
        <Button
          variant="secondary"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onPageChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}

// --- Page header ----------------------------------------------------------------------------

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 text-sm text-muted">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

// --- Toasts ------------------------------------------------------------------------------------

type Toast = { id: number; tone: "success" | "error"; message: string };

const ToastContext = createContext<{
  push: (tone: "success" | "error", message: string) => void;
}>({ push: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: "success" | "error", message: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, tone, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-80 flex-col gap-2"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`ta-toast-in pointer-events-auto flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm shadow-lg ${
              t.tone === "success"
                ? "border-success/25 bg-surface text-foreground"
                : "border-danger/25 bg-surface text-foreground"
            }`}
          >
            {t.tone === "success" ? (
              <IconCheck size={15} className="mt-0.5 shrink-0 text-success" />
            ) : (
              <IconAlert size={15} className="mt-0.5 shrink-0 text-danger" />
            )}
            <p className="leading-snug">{t.message}</p>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
