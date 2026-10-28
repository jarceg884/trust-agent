"use client";

/**
 * Sección "Security" del operador (spec 025, REQ-PS-009, D-PS-05).
 *
 * MFA TOTP opcional: banner que lo sugiere siempre mientras no esté activo,
 * enrolamiento en dos pasos (secret one-time → verificación con un código
 * real ANTES de activar) y desactivación que exige password + código.
 * Todo el tráfico pasa por el proxy BFF (cookie httpOnly).
 */
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Field,
  Input,
  OneTimeSecret,
  PageHeader,
  useToast,
} from "@/components/ui";

type MfaStatus = "idle" | "enrolling" | "active";

interface EnrollStartResponse {
  secret: string;
  otpauth_url: string;
}

async function postJson(path: string, body?: unknown) {
  const res = await fetch(`/api/proxy${path}`, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    // respuesta no-JSON
  }
  if (!res.ok) {
    const env = (payload ?? {}) as { error?: { message?: string } };
    throw new Error(env.error?.message ?? `Request failed (HTTP ${res.status}).`);
  }
  return payload;
}

export function SecurityClient({
  username,
  mfaEnrolled,
}: {
  username: string;
  mfaEnrolled: boolean;
}) {
  const { push } = useToast();
  const [status, setStatus] = useState<MfaStatus>(mfaEnrolled ? "active" : "idle");
  const [enroll, setEnroll] = useState<EnrollStartResponse | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDisable, setShowDisable] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");

  async function startEnrollment() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const payload = (await postJson("/provider/mfa/enroll-start")) as EnrollStartResponse;
      setEnroll(payload);
      setCode("");
      setStatus("enrolling");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unexpected error.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmEnrollment(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await postJson("/provider/mfa/enroll-confirm", { code: code.trim() });
      setStatus("active");
      setEnroll(null);
      setCode("");
      push("success", "TOTP MFA is now active. Sign-in will ask for your code.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unexpected error.");
    } finally {
      setBusy(false);
    }
  }

  function cancelEnrollment() {
    // El secret pendiente queda inactivo e inutilizable hasta re-enrolar
    // (enroll-start rota el secret). No se activa nada sin verificar.
    setStatus("idle");
    setEnroll(null);
    setCode("");
    setError(null);
  }

  async function disableMfa(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !disablePassword || !disableCode.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await postJson("/provider/mfa/disable", {
        password: disablePassword,
        code: disableCode.trim(),
      });
      setStatus("idle");
      setShowDisable(false);
      setDisablePassword("");
      setDisableCode("");
      push("success", "TOTP MFA disabled. We recommend re-enrolling it.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unexpected error.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Security"
        description={`Account security for the provider operator ${username}.`}
        actions={
          status === "active" ? (
            <Badge tone="success">MFA active</Badge>
          ) : (
            <Badge tone="warning">MFA not enrolled</Badge>
          )
        }
      />

      {error ? (
        <p
          role="alert"
          className="mb-4 rounded-lg border border-danger/25 bg-danger-soft px-4 py-3 text-sm text-danger"
        >
          {error}
        </p>
      ) : null}

      {status !== "active" ? (
        <div
          role="status"
          className="mb-6 rounded-xl border border-warning/25 bg-warning-soft px-4 py-3.5 text-sm leading-relaxed text-warning"
        >
          <strong>Recommended:</strong> enroll two-factor authentication
          (TOTP). Your operator account controls every tenant in the platform —
          a second factor protects it even if your password leaks. It is
          optional, but we suggest keeping it on.
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Two-factor authentication (TOTP)"
            description="RFC 6238 — 6 digits, 30-second step. Works with any authenticator app."
          />
          <CardBody className="space-y-4">
            {status === "idle" ? (
              <>
                <p className="text-sm leading-relaxed text-muted">
                  No TOTP factor is enrolled. Enrolling generates a one-time
                  secret that you add to your authenticator app, then verify a
                  code before activation.
                </p>
                <Button onClick={startEnrollment} disabled={busy}>
                  {busy ? "Generating…" : "Enable TOTP MFA"}
                </Button>
              </>
            ) : null}

            {status === "enrolling" && enroll ? (
              <form onSubmit={confirmEnrollment} className="space-y-4" noValidate>
                <p className="text-sm leading-relaxed text-muted">
                  Add this secret to your authenticator app (manual entry or
                  the <code className="font-mono text-xs">otpauth://</code>{" "}
                  link), then enter the 6-digit code it generates to confirm.
                </p>
                <OneTimeSecret value={enroll.secret} />
                <div className="flex items-center gap-2">
                  <code className="min-w-0 flex-1 truncate rounded-lg border border-border-subtle bg-surface-alt px-3 py-2.5 font-mono text-xs text-foreground">
                    {enroll.otpauth_url}
                  </code>
                </div>
                <Field
                  label="Verification code"
                  htmlFor="totp-code"
                  hint="The 6-digit code your authenticator shows right now."
                >
                  <Input
                    id="totp-code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder="000000"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                </Field>
                <div className="flex items-center gap-2">
                  <Button type="submit" disabled={busy || !code.trim()}>
                    {busy ? "Verifying…" : "Verify and activate"}
                  </Button>
                  <Button variant="secondary" onClick={cancelEnrollment} disabled={busy}>
                    Cancel
                  </Button>
                </div>
              </form>
            ) : null}

            {status === "active" ? (
              <>
                <p className="text-sm leading-relaxed text-muted">
                  A TOTP factor is active: every sign-in asks for your
                  6-digit code after the password. Disabling it requires your
                  password and a valid code.
                </p>
                {!showDisable ? (
                  <Button variant="danger" onClick={() => setShowDisable(true)}>
                    Disable MFA
                  </Button>
                ) : (
                  <form onSubmit={disableMfa} className="space-y-4" noValidate>
                    <Field label="Current password" htmlFor="disable-password">
                      <Input
                        id="disable-password"
                        type="password"
                        autoComplete="current-password"
                        value={disablePassword}
                        onChange={(e) => setDisablePassword(e.target.value)}
                      />
                    </Field>
                    <Field label="TOTP code" htmlFor="disable-code">
                      <Input
                        id="disable-code"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        placeholder="000000"
                        value={disableCode}
                        onChange={(e) => setDisableCode(e.target.value)}
                      />
                    </Field>
                    <div className="flex items-center gap-2">
                      <Button variant="danger" type="submit" disabled={busy}>
                        {busy ? "Disabling…" : "Confirm disable"}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => setShowDisable(false)}
                        disabled={busy}
                      >
                        Keep MFA
                      </Button>
                    </div>
                  </form>
                )}
              </>
            ) : null}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Password policy"
            description="Applies to the provider operator account."
          />
          <CardBody className="space-y-3 text-sm leading-relaxed text-muted">
            <p>
              Passwords are stored as Argon2id hashes (PHC string) — never in
              plain text. The bootstrap password is one-time and must be
              changed on first sign-in.
            </p>
            <p>
              Failed sign-ins are throttled persistently (PostgreSQL), per
              account and per IP. Every attempt — success or denial — is
              recorded in the provider audit log.
            </p>
            <p>
              Sessions live in PostgreSQL and survive API restarts; the
              session token only travels inside an httpOnly cookie set by
              this console&apos;s server.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
