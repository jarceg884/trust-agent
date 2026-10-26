"use client";

/**
 * Flujo de enrolamiento del dispositivo (spec 007 desde el lado cliente).
 *
 * Keystore local (D-DTB-04): keypair RSA-2048 generado con WebCrypto en el
 * navegador; la privada (PKCS#8) permanece en localStorage (v1 web; keychain
 * del SO en el empaquetado). El enrollment_token one-time se canjea exactamente
 * una vez; mientras el admin aprueba, el canje repite 202 sin consumir.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Brand } from "@/components/brand";
import { Badge, Card, ErrorState, StatusDot } from "@/components/ui";
import type { MyDevice } from "@/lib/control-plane";

const KEYSTORE_KEY = "ta_device_keystore_v1";
const POLL_INTERVAL_MS = 5000;

interface Keystore {
  fingerprint: string;
  device_id: string;
  public_key_pem: string;
  private_key_pkcs8_b64: string;
  device_token?: string;
  enrolled_at?: string;
}

type Phase =
  | "loading"
  | "idle"
  | "generating"
  | "pending"
  | "active"
  | "gone"
  | "error";

function readKeystore(): Keystore | null {
  try {
    const raw = window.localStorage.getItem(KEYSTORE_KEY);
    return raw ? (JSON.parse(raw) as Keystore) : null;
  } catch {
    return null;
  }
}

function writeKeystore(store: Keystore) {
  window.localStorage.setItem(KEYSTORE_KEY, JSON.stringify(store));
}

function b64(buffer: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

function spkiToPem(spki: ArrayBuffer): string {
  const b64Body = b64(spki).replace(/(.{64})/g, "$1\n").replace(/\n$/, "");
  return `-----BEGIN PUBLIC KEY-----\n${b64Body}\n-----END PUBLIC KEY-----\n`;
}

/** Fingerprint estable por instalación (v1 web; en empaquetado: hostname+OS). */
function stableFingerprint(): string {
  const uuid =
    globalThis.crypto?.randomUUID?.() ??
    `x-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  const platform =
    typeof navigator !== "undefined" ? navigator.platform || "web" : "web";
  return `web-${platform.toLowerCase().replace(/\s+/g, "-")}-${uuid}`;
}

export function EnrollFlow() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<{ message: string; requestId?: string | null } | null>(
    null,
  );
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [pollCount, setPollCount] = useState(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  // Estado inicial: ¿este dispositivo (keystore local) ya está enrolado?
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const keystore = readKeystore();
      try {
        const res = await fetch("/api/desktop/state", { cache: "no-store" });
        if (res.status === 401) {
          window.location.href = "/";
          return;
        }
        const data = (await res.json()) as { devices?: MyDevice[] };
        const mine = (data.devices ?? []).find(
          (d) => keystore && d.fingerprint === keystore.fingerprint,
        );
        if (cancelled) return;
        if (mine?.status === "active") {
          setPhase("active");
          setDeviceId(mine.id);
        } else if (mine?.status === "pending" && keystore?.device_id) {
          setDeviceId(keystore.device_id);
          setPhase("pending");
        } else {
          setPhase("idle");
        }
      } catch {
        if (!cancelled) setPhase("idle");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Polling del canje (D-DEV-04): 202 sigue esperando; 200 entrega device token.
  const pollExchange = useCallback(
    async (enrollmentToken: string) => {
      try {
        const res = await fetch("/api/desktop/exchange", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enrollment_token: enrollmentToken }),
        });
        setPollCount((n) => n + 1);

        if (res.status === 202) {
          // Sigue pending: repetir sin consumir.
          pollTimer.current = setTimeout(
            () => void pollExchange(enrollmentToken),
            POLL_INTERVAL_MS,
          );
          return;
        }
        if (res.status === 200) {
          const data = (await res.json()) as { device_token: string };
          const keystore = readKeystore();
          if (keystore) {
            writeKeystore({
              ...keystore,
              device_token: data.device_token,
              enrolled_at: new Date().toISOString(),
            });
          }
          setPhase("active");
          return;
        }
        if (res.status === 410) {
          // Uniforme, sin oráculo (D-DEV-03): rechazado/revocado/consumido.
          setPhase("gone");
          return;
        }
        const data = (await res.json()) as {
          error?: { message?: string; request_id?: string };
        };
        setError({
          message: data.error?.message ?? "Enrollment check failed.",
          requestId: data.error?.request_id ?? null,
        });
        setPhase("error");
      } catch {
        // Red caída: reintentar el polling (el canje no se consumió).
        pollTimer.current = setTimeout(
          () => void pollExchange(enrollmentToken),
          POLL_INTERVAL_MS * 2,
        );
      }
    },
    [],
  );

  useEffect(() => stopPolling, [stopPolling]);

  const requestEnrollment = useCallback(async () => {
    setPhase("generating");
    setError(null);
    try {
      // 1) Keypair local: la privada no sale del dispositivo (D-DEV-01).
      const keyPair = await globalThis.crypto.subtle.generateKey(
        {
          name: "RSA-OAEP",
          modulusLength: 2048,
          publicExponent: new Uint8Array([1, 0, 1]),
          hash: "SHA-256",
        },
        true,
        ["encrypt", "decrypt"],
      );
      const spki = await globalThis.crypto.subtle.exportKey("spki", keyPair.publicKey);
      const pkcs8 = await globalThis.crypto.subtle.exportKey("pkcs8", keyPair.privateKey);
      const publicKeyPem = spkiToPem(spki);

      const existing = readKeystore();
      const fingerprint = existing?.fingerprint ?? stableFingerprint();

      // 2) Solicitud de enrolamiento (el id_token lo inyecta el route handler).
      const res = await fetch("/api/desktop/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          public_key: publicKeyPem,
          fingerprint,
          device_name:
            typeof navigator !== "undefined"
              ? `Trust Agent on ${navigator.platform || "Web"}`
              : "Trust Agent Desktop",
          platform: "web",
        }),
      });

      if (res.status === 202) {
        const data = (await res.json()) as {
          device_id: string;
          enrollment_token: string;
        };
        writeKeystore({
          fingerprint,
          device_id: data.device_id,
          public_key_pem: publicKeyPem,
          private_key_pkcs8_b64: b64(pkcs8),
        });
        setDeviceId(data.device_id);
        setPhase("pending");
        void pollExchange(data.enrollment_token);
        return;
      }

      const data = (await res.json()) as {
        error?: { message?: string; reason?: string; request_id?: string };
      };
      setError({
        message:
          data.error?.message ?? "The organization rejected the enrollment request.",
        requestId: data.error?.request_id ?? null,
      });
      setPhase("error");
    } catch {
      setError({
        message:
          "Could not generate the device key or reach the control plane. Try again.",
      });
      setPhase("error");
    }
  }, [pollExchange]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 py-12">
      <div className="w-full max-w-lg space-y-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <Brand />
          <p className="text-sm text-muted">Device enrollment</p>
        </div>

        <Card>
          {phase === "loading" && (
            <p className="py-6 text-center text-sm text-muted">
              Checking this device…
            </p>
          )}

          {phase === "idle" && (
            <div className="space-y-4">
              <h1 className="text-lg font-semibold">
                Enroll this device to your organization
              </h1>
              <ol className="list-decimal space-y-1.5 pl-5 text-sm text-muted">
                <li>A device key pair is generated locally on this machine.</li>
                <li>
                  Your organization admin approves the enrollment request.
                </li>
                <li>
                  This device receives a signed device token and joins the
                  governed session.
                </li>
              </ol>
              <button
                type="button"
                onClick={() => void requestEnrollment()}
                className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition hover:brightness-110"
              >
                Generate key & request enrollment
              </button>
            </div>
          )}

          {phase === "generating" && (
            <p className="py-6 text-center text-sm text-muted">
              Generating device key pair…
            </p>
          )}

          {phase === "pending" && (
            <div className="space-y-4" role="status">
              <div className="flex items-center justify-between">
                <h1 className="text-lg font-semibold">
                  Waiting for approval
                </h1>
                <Badge tone="warning">
                  <StatusDot tone="warning" /> pending
                </Badge>
              </div>
              <p className="text-sm text-muted">
                Your organization admin has been notified. This page checks for
                approval automatically every {POLL_INTERVAL_MS / 1000} seconds
                ({pollCount} check{pollCount === 1 ? "" : "s"} so far).
              </p>
              {deviceId && (
                <p className="rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 font-mono text-xs text-muted">
                  device_id: {deviceId}
                </p>
              )}
              <div
                aria-hidden
                className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-border-subtle border-t-accent"
              />
            </div>
          )}

          {phase === "active" && (
            <div className="space-y-4" role="status">
              <div className="flex items-center justify-between">
                <h1 className="text-lg font-semibold">Device enrolled</h1>
                <Badge tone="success">
                  <StatusDot tone="success" /> active
                </Badge>
              </div>
              <p className="text-sm text-muted">
                This device is now part of your organization&apos;s governed
                session. Only organization-approved models, budgets and
                policies apply here.
              </p>
              {deviceId && (
                <p className="rounded-lg border border-border-subtle bg-surface px-3.5 py-2.5 font-mono text-xs text-muted">
                  device_id: {deviceId}
                </p>
              )}
              <a
                href="/dashboard"
                className="block w-full rounded-lg bg-accent px-4 py-2.5 text-center text-sm font-semibold text-white transition hover:brightness-110"
              >
                Go to dashboard
              </a>
            </div>
          )}

          {phase === "gone" && (
            <div className="space-y-4">
              <ErrorState message="This enrollment is no longer valid. Start again or contact your organization admin." />
              <button
                type="button"
                onClick={() => setPhase("idle")}
                className="w-full rounded-lg border border-border-subtle px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-panel-hover"
              >
                Try again
              </button>
            </div>
          )}

          {phase === "error" && (
            <div className="space-y-4">
              <ErrorState
                message={error?.message ?? "Something went wrong."}
                requestId={error?.requestId}
              />
              <button
                type="button"
                onClick={() => setPhase("idle")}
                className="w-full rounded-lg border border-border-subtle px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-panel-hover"
              >
                Back
              </button>
            </div>
          )}
        </Card>

        <p className="text-center text-xs text-muted">
          The private key never leaves this device. The organization only
          receives your public key and a device fingerprint.
        </p>
      </div>
    </main>
  );
}
