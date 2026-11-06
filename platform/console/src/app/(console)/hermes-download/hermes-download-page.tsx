"use client";

/**
 * Hermes Download — sección del Admin Console del cliente (t_360a752e).
 *
 * - Link estable del tenant (spec 023): construido server-side, copiable y
 *   enviable a empleados (mensaje de invitación copiable / borrador de email).
 * - Disponibilidad del link (enable/disable) contra el contrato
 *   `GET/PUT /api/download-link` (spec 023, t_526030f3). Mientras el
 *   endpoint no exista en el control plane, la UI muestra el estado REAL
 *   (404) con banner honesto — nada simulado (constitution I).
 * - Gate de rol: solo org_admin (decisión server-side en page.tsx).
 *
 * ESTÁNDAR UI PRO (mandato Miller, t_7cd4f40e): iconos lucide-react y
 * animaciones con propósito — entrada escalonada (`ta-rise`, definida en
 * globals.css) que guía la lectura del flujo link → enviar → disponibilidad
 * → pasos, y micro-transiciones en los interactivos. Respeta
 * prefers-reduced-motion.
 */
import { useState } from "react";
import {
  Check,
  Copy,
  ExternalLink,
  Link2,
  Mail,
  MousePointerClick,
  KeyRound,
  ShieldCheck,
  Terminal,
  Download,
  Scale,
} from "lucide-react";
import { useApi, useMutation } from "@/hooks/use-api";
import { ApiError } from "@/lib/api-client";
import { dateTime } from "@/lib/format";
import type { DownloadLinkStatus } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  CopyButton,
  ErrorState,
  Field,
  Input,
  Skeleton,
  useToast,
} from "@/components/ui";

function inviteMessage(orgCode: string, downloadUrl: string): string {
  return (
    `Download Trust Agent for our organization (${orgCode}):\n\n` +
    `Desktop app:\n  ${downloadUrl}\n\n` +
    `CLI (server / VPS / terminal — Linux, macOS, WSL2):\n  ` +
    `curl -fsSL ${downloadUrl}/cli | bash\n\n` +
    `Install the desktop app, then activate it with the one-time enrollment ` +
    `token your admin generates in Users & Roles (it is shown exactly once). ` +
    `First launch asks for: portal URL, organization (${orgCode}) and token. ` +
    `No approval step is needed — the admin's token IS the authorization, and ` +
    `from that moment the agent runs under the organization's governance policies.`
  );
}

/** Retardo de entrada escalonada por índice de tarjeta (ta-rise). */
function rise(index: number) {
  return {
    className: "ta-rise",
    style: { animationDelay: `${index * 70}ms` },
  };
}

export function HermesDownloadPage({
  isAdmin,
  orgCode,
  downloadUrl,
}: {
  isAdmin: boolean;
  orgCode: string;
  downloadUrl: string;
}) {
  const { push } = useToast();
  const [email, setEmail] = useState("");
  const [confirmDisable, setConfirmDisable] = useState(false);

  // Estado REAL del link contra el contrato de la spec 023.
  const link = useApi<DownloadLinkStatus>("/api/download-link");
  const toggle = useMutation();

  // El endpoint de gestión aún no existe en el control plane (spec 023 en
  // vuelo): 404/405 = contrato pendiente, no un error del usuario.
  const linkApiPending =
    link.error !== null &&
    (link.error.status === 404 || link.error.status === 405);

  if (!isAdmin) {
    return (
      <Card {...rise(0)}>
        <CardHeader
          title="Organization admins only"
          description="Managing the desktop download link is restricted to organization admins."
        />
        <CardBody>
          <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
            <div className="flex size-11 items-center justify-center rounded-full bg-surface-alt text-muted">
              <ShieldCheck size={20} />
            </div>
            <div>
              <p className="text-sm font-semibold text-foreground">
                You don&apos;t have permission to view this
              </p>
              <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-muted">
                Ask an organization admin to share the download link with you.
                Only the organization admin role can view and manage this
                section.
              </p>
            </div>
          </div>
        </CardBody>
      </Card>
    );
  }

  async function copyInviteMessage() {
    const message = inviteMessage(orgCode, downloadUrl);
    try {
      await navigator.clipboard.writeText(message);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = message;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    push("success", "Invite message copied — paste it in your email client.");
  }

  function openEmailDraft() {
    const subject = `Download Trust Agent — ${orgCode}`;
    const body = inviteMessage(orgCode, downloadUrl);
    window.location.href = `mailto:${encodeURIComponent(email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    push("success", `Email draft opened for ${email || "the employee"}.`);
  }

  async function setEnabled(enabled: boolean) {
    try {
      await toggle.mutate("/api/download-link", { enabled }, "PUT");
      push(
        "success",
        enabled
          ? "Download link enabled — employees can download the app again."
          : "Download link disabled — the public download page now refuses new downloads.",
      );
      link.refetch();
    } catch (cause) {
      const message =
        cause instanceof ApiError
          ? `${cause.message} (${cause.reason})`
          : "The link update failed.";
      push("error", message);
    } finally {
      setConfirmDisable(false);
    }
  }

  const steps = [
    {
      icon: MousePointerClick,
      title: "Open the link",
      body: "The public download page shows your organization's branding — no login required to download.",
    },
    {
      icon: Download,
      title: "Install the app",
      body: "The desktop bundle arrives pre-bound to your organization code (portal URL and org are already filled in).",
    },
    {
      icon: KeyRound,
      title: "Paste your enrollment token",
      body: "An admin generates a one-time token in Users & Roles → Enrollment token. Paste it on first launch — it is shown exactly once and authorizes this device.",
    },
    {
      icon: Scale,
      title: "Governed from day one",
      body: "Activation is immediate (no approval queue): the agent runs under your policies — approved models, allowed tools, budget with cutoff, full audit trail.",
    },
  ];

  return (
    <div className="space-y-6">
      {/* Link estable del tenant */}
      <Card {...rise(0)}>
        <CardHeader
          title="Stable download link"
          description="One permanent link for your whole organization. The download page carries your branding and serves the desktop bundle already bound to this organization."
          actions={
            <Badge tone="neutral">
              <Link2 size={11} className="mr-1" />
              org: {orgCode}
            </Badge>
          }
        />
        <CardBody className="space-y-4">
          {/* Pulido v2 r3 (crítica visual: la URL "leía como input disabled"):
              campo blanco con la URL a contraste real — host en muted, ruta
              del tenant en bold. La URL completa (construida server-side)
              es la que copia el botón primario. */}
          <div className="flex items-center gap-2">
            <code
              className="min-w-0 flex-1 truncate rounded-lg border border-border-subtle bg-surface px-3 py-2.5 font-mono text-xs transition-colors duration-200 hover:border-foreground/25"
              title={downloadUrl}
            >
              <span className="text-muted">{downloadUrl.split("/download/")[0]}</span>
              <span className="font-semibold text-foreground">
                /download/{downloadUrl.split("/download/")[1] ?? ""}
              </span>
            </code>
            {/* r3: "Copy link" es LA acción primaria de la página — filled
                accent (crítica visual: "make the primary action filled-
                accent, demote the rest"). */}
            <CopyButton value={downloadUrl} label="Copy link" variant="primary" />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a href={downloadUrl} target="_blank" rel="noreferrer">
              <Button variant="secondary" size="sm">
                <ExternalLink size={13} /> Open download page
              </Button>
            </a>
            <span className="text-xs text-muted">
              The link never changes — safe to pin in your intranet or
              onboarding docs.
            </span>
          </div>
        </CardBody>
      </Card>

      {/* CLI installation (spec 034, mandato Miller): mismo agente gobernado
          para el empleado de servidor/VPS/terminal — comando copiable. */}
      <Card {...rise(1)}>
        <CardHeader
          title="CLI installation"
          description="For employees working on a server, VPS or terminal: one command installs the governed CLI, already bound to this organization. Same enrollment, same policies, same audit as the desktop app."
          actions={
            <Badge tone="neutral">
              <Terminal size={11} className="mr-1" />
              Linux · macOS · WSL2
            </Badge>
          }
        />
        <CardBody className="space-y-4">
          <div className="flex items-center gap-2">
            <code
              className="min-w-0 flex-1 truncate rounded-lg border border-border-subtle bg-surface px-3 py-2.5 font-mono text-xs transition-colors duration-200 hover:border-foreground/25"
              title={`${downloadUrl}/cli`}
            >
              <span className="text-muted">curl -fsSL </span>
              <span className="font-semibold text-foreground">
                {downloadUrl}/cli
              </span>
              <span className="text-muted"> | bash</span>
            </code>
            <CopyButton
              value={`curl -fsSL ${downloadUrl}/cli | bash`}
              label="Copy command"
              variant="primary"
            />
          </div>
          <p className="text-xs leading-relaxed text-muted">
            The installer adds the CLI if it is missing, plus the governance
            plugin and your branding. The device shows up under Devices as type
            <span className="font-semibold text-foreground">CLI</span> —
            you approve it exactly like a desktop.
          </p>
        </CardBody>
      </Card>

      {/* Enviar a un empleado */}
      <Card {...rise(2)}>
        <CardHeader
          title="Send the link to an employee"
          description="Generate a ready-to-send invitation with the link and the enrollment steps. Sending happens from your own email client — no email is sent by this console."
        />
        <CardBody className="space-y-4">
          <Field
            label="Employee email"
            htmlFor="employee-email"
            hint="Used only to pre-fill the draft on your machine — nothing is stored or sent by the console."
          >
            <Input
              id="employee-email"
              type="email"
              placeholder="name@yourcompany.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            {/* r3: jerarquía por tarjeta — "Copy invite message" secundario
                (borde), "Open email draft" ghost: cada tarjeta tiene UN
                peso dominante (crítica visual: "the invite card has no
                primary action at all"). */}
            <Button variant="secondary" size="sm" onClick={copyInviteMessage}>
              <Copy size={13} /> Copy invite message
            </Button>
            <Button variant="ghost" size="sm" onClick={openEmailDraft}>
              <Mail size={13} /> Open email draft
            </Button>
          </div>
        </CardBody>
      </Card>

      {/* Disponibilidad del link (enable/disable) — contrato spec 023 */}
      <Card {...rise(3)}>
        <CardHeader
          title="Link availability"
          description="Disable the public download page to stop new downloads (e.g. during an incident). Already-enrolled devices are not affected."
        />
        <CardBody>
          {linkApiPending ? (
            /* Pulido v2 (hallazgo #6): copy orientado al usuario — sin
               jerga de ingeniería (specs, endpoints). El estado sigue
               siendo el REAL (404 del contrato pendiente), solo cambia
               cómo se explica. Tono info: es un aviso, no una alarma. */
            <div
              role="status"
              className="rounded-xl border border-info/25 bg-info-soft px-5 py-4"
            >
              <p className="flex items-center gap-2 text-sm font-semibold text-info">
                <ShieldCheck size={14} />
                Link availability controls are coming soon
              </p>
              <p className="mt-1 text-xs leading-relaxed text-info">
                Your download link above is live and ready to share — no
                action needed. Soon you&apos;ll also be able to pause new
                downloads from this page (for example, during an incident)
                without affecting devices that are already enrolled. The
                control will appear here automatically once it rolls out.
              </p>
            </div>
          ) : link.loading ? (
            <div className="space-y-3">
              <Skeleton className="h-5 w-1/3" />
              <Skeleton className="h-9 w-48" />
            </div>
          ) : link.error ? (
            <ErrorState
              error={link.error}
              onRetry={link.refetch}
              context="GET /api/download-link"
            />
          ) : link.data ? (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <Badge tone={link.data.enabled ? "success" : "danger"}>
                  {link.data.enabled ? "Enabled" : "Disabled"}
                </Badge>
                <span className="text-xs text-muted">
                  Last changed {dateTime(link.data.updated_at)}
                </span>
              </div>
              {link.data.enabled ? (
                <Button
                  variant="danger"
                  size="sm"
                  loading={toggle.submitting}
                  onClick={() => setConfirmDisable(true)}
                >
                  Disable link
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  loading={toggle.submitting}
                  onClick={() => setEnabled(true)}
                >
                  <Check size={13} /> Enable link
                </Button>
              )}
            </div>
          ) : null}
        </CardBody>
      </Card>

      {/* Cómo funciona el flujo para el empleado */}
      <Card {...rise(4)}>
        <CardHeader
          title="What the employee experiences"
          description="The download binds the app to your organization from the very first run."
        />
        <CardBody>
          <ol className="space-y-3">
            {steps.map((step, i) => (
              <li
                key={step.title}
                className="group flex gap-3 rounded-lg p-2 transition-colors duration-200 hover:bg-surface-alt"
              >
                {/* Pulido v2 r2: círculo más grande con borde sutil — a 28px
                    el icono se leía "muddy" (crítica visual #8/iconografía). */}
                <span
                  aria-hidden="true"
                  className="flex size-9 shrink-0 items-center justify-center rounded-full border border-accent-border bg-accent-soft text-accent transition-transform duration-200 group-hover:scale-105"
                >
                  <step.icon size={16} />
                </span>
                <div>
                  <p className="text-sm font-medium text-foreground">
                    <span className="mr-1.5 font-mono text-[11px] text-faint">
                      {i + 1}.
                    </span>
                    {step.title}
                  </p>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted">
                    {step.body}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </CardBody>
      </Card>

      <ConfirmDialog
        open={confirmDisable}
        onClose={() => setConfirmDisable(false)}
        onConfirm={() => setEnabled(false)}
        loading={toggle.submitting}
        danger
        title="Disable the download link?"
        confirmLabel="Disable link"
        message="The public download page will refuse new downloads for your organization until you re-enable it. Already-enrolled devices keep working. This action is audited."
      />
    </div>
  );
}
