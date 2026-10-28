"use client";

/**
 * Pill de integridad de la cadena de audit (spec 021, D-21-02 / REQ-21-004).
 *
 * Estado SIEMPRE visible en la cabecera de la página Audit:
 *   - org_admin: al montar, UNA llamada real a POST /api/admin/audit/verify por
 *     sesión de navegador (guard en sessionStorage — cada verificación emite
 *     audit.verified en el backend, D-AU-05; el guard evita emitir una por
 *     render). El botón "Verify chain" re-verifica on-demand sin límite.
 *   - sin org_admin: pill neutral (el endpoint exige el rol, spec 008).
 * Verde = Chain intact · rojo = Chain broken at seq N.
 *
 * Nota lint: el estado se actualiza SOLO en callbacks async de la promesa
 * (regla react-hooks/set-state-in-effect, mismo patrón que use-api.ts).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api-client";
import type { ChainVerifyResult } from "@/lib/types";
import { Badge, Button, useToast } from "@/components/ui";
import { IconRefresh, IconShield } from "@/components/icons";

const GUARD_KEY = "ta_audit_verified_at";

type IntegrityState =
  | { kind: "idle" }
  | { kind: "verifying" }
  | { kind: "ok" }
  | { kind: "broken"; seq: number | null }
  | { kind: "forbidden" }
  | { kind: "error"; message: string };

export function AuditIntegrity({
  isAdmin,
  onVerified,
}: {
  isAdmin: boolean;
  /** Refresca el log: cada verificación añade un audit.verified real. */
  onVerified: () => void;
}) {
  const [state, setState] = useState<IntegrityState>({ kind: "idle" });
  const { push } = useToast();
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /** Llama al verificador real y resuelve el estado en los callbacks async
   *  (patrón de use-api.ts: setState solo dentro de .then/.catch). */
  const performVerify = useCallback(() => {
    return api<ChainVerifyResult>("/api/admin/audit/verify", {
      method: "POST",
    })
      .then((result) => {
        if (!mounted.current) return;
        if (result.status === "ok") {
          setState({ kind: "ok" });
        } else {
          setState({ kind: "broken", seq: result.seq ?? null });
        }
        onVerified();
      })
      .catch((cause: unknown) => {
        if (!mounted.current) return;
        if (cause instanceof ApiError && cause.status === 403) {
          setState({ kind: "forbidden" });
          return;
        }
        setState({
          kind: "error",
          message:
            cause instanceof ApiError ? cause.message : "Verification failed.",
        });
      });
  }, [onVerified]);

  // Auto-verificación 1× por sesión de navegador (solo org_admin). El estado
  // se resuelve en los callbacks de la promesa; el pill "idle" es el estado
  // transitorio hasta que el verificador responde.
  useEffect(() => {
    if (!isAdmin) return;
    try {
      if (sessionStorage.getItem(GUARD_KEY)) return;
      sessionStorage.setItem(GUARD_KEY, String(Date.now()));
    } catch {
      // sessionStorage no disponible (p. ej. bloqueado): se verifica igual.
    }
    void performVerify();
  }, [isAdmin, performVerify]);

  /** Verificación manual (event handler): muestra el estado "verifying". */
  const verifyNow = useCallback(() => {
    setState({ kind: "verifying" });
    void performVerify();
  }, [performVerify]);

  if (state.kind === "verifying") {
    return (
      <Badge tone="info" aria-live="polite">
        <IconShield size={12} /> Verifying chain…
      </Badge>
    );
  }
  if (state.kind === "ok") {
    return (
      <span className="inline-flex items-center gap-2" role="status">
        <Badge tone="success">
          <IconShield size={12} /> Chain intact
        </Badge>
        <VerifyButton onClick={verifyNow} />
      </span>
    );
  }
  if (state.kind === "broken") {
    return (
      <span className="inline-flex items-center gap-2" role="alert">
        <Badge tone="danger">
          <IconShield size={12} />
          Chain broken{state.seq != null ? ` at seq ${state.seq}` : ""}
        </Badge>
        <VerifyButton onClick={verifyNow} />
      </span>
    );
  }
  if (state.kind === "forbidden") {
    return (
      <Badge tone="neutral">
        <IconShield size={12} /> Verification requires the org admin role
      </Badge>
    );
  }
  if (state.kind === "error") {
    return (
      <span className="inline-flex items-center gap-2" role="alert">
        <Badge tone="warning">
          <IconShield size={12} /> Verification unavailable
        </Badge>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            push("error", state.message);
            verifyNow();
          }}
        >
          <IconRefresh size={13} /> Retry
        </Button>
      </span>
    );
  }
  // idle (aún sin resultado en esta sesión): neutral con verificación manual.
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone="neutral">
        <IconShield size={12} /> Chain not verified in this session
      </Badge>
      {isAdmin ? <VerifyButton onClick={verifyNow} /> : null}
    </span>
  );
}

function VerifyButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="secondary" size="sm" onClick={onClick}>
      <IconShield size={13} /> Verify chain
    </Button>
  );
}
