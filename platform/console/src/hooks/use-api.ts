"use client";

/**
 * Hooks de datos (spec 009 plan §2): `useApi` para lecturas con estados
 * loading/error reales, `useMutation` para acciones con feedback.
 * Cero cache client-side de datos de gobernanza (constitution I): refetch
 * explícito tras cada mutación.
 *
 * Nota de implementación: el estado se actualiza SOLO en los callbacks
 * async del fetch (regla react-hooks/set-state-in-effect — sin setState
 * síncrono en efectos); "loading" se DERIVA comparando la key de la
 * request en curso con la key del estado almacenado.
 *
 * Refetch silencioso (spec 042 REQ-PE-007, patrón live de la spec 027):
 * con `{ silentRefetch: true }`, un `refetch()` de la MISMA ruta no vuelve
 * al estado loading — la UI sigue mostrando el dato previo mientras la
 * request vuela (`refreshing: true`) y al resolver solo cambian las
 * cifras: cero skeleton, cero flicker, filtros/paginación intactos. Si el
 * refetch silencioso falla, se CONSERVA el último dato (se expone el error
 * junto al dato; la página decide cómo mostrarlo) — un blip transitorio
 * no puede tirar la vista en vivo.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api-client";

export interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  /** true mientras un refetch silencioso de la ruta actual está en vuelo. */
  refreshing: boolean;
  error: ApiError | null;
  refetch: () => void;
}

export interface UseApiOptions {
  /** Ver nota del módulo: refetch de la misma ruta sin skeleton/flicker. */
  silentRefetch?: boolean;
}

interface FetchState<T> {
  key: string;
  /** Ruta del resultado almacenado ("" = aún sin resultado). */
  path: string;
  status: "done" | "error";
  data: T | null;
  error: ApiError | null;
}

export function useApi<T>(
  path: string | null,
  opts?: UseApiOptions,
): UseApiState<T> {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<FetchState<T>>({
    key: "",
    path: "",
    status: "done",
    data: null,
    error: null,
  });
  const mounted = useRef(true);
  const silent = Boolean(opts?.silentRefetch);
  const key = `${path ?? "\u0000null"}#${tick}`;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal })
      .then((payload) => {
        if (!mounted.current) return;
        setState({ key, path, status: "done", data: payload, error: null });
      })
      .catch((cause) => {
        if (!mounted.current || controller.signal.aborted) return;
        setState((prev) => ({
          key,
          path,
          status: "error",
          // Silencioso + misma ruta: el dato previo sobrevive al error.
          data: silent && prev.path === path ? prev.data : null,
          error:
            cause instanceof ApiError
              ? cause
              : new ApiError(0, "unknown", String(cause), null),
        }));
      });
    return () => controller.abort();
  }, [path, key, silent]);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  // Derivación: la request en curso (key) aún sin resultado → loading,
  // salvo que sirva el dato previo de la MISMA ruta (refetch silencioso).
  if (path === null) {
    return { data: null, loading: false, refreshing: false, error: null, refetch };
  }
  const settled = state.key === key;
  const staleUsable = silent && state.path === path && state.data !== null;
  if (settled || staleUsable) {
    return {
      data: state.data,
      loading: false,
      refreshing: !settled,
      error: settled ? state.error : null,
      refetch,
    };
  }
  return { data: null, loading: true, refreshing: false, error: null, refetch };
}

export interface MutationState {
  submitting: boolean;
  error: ApiError | null;
}

/** Envoltura de mutación con estado de submit/error compartido por diálogos. */
export function useMutation(): MutationState & {
  mutate: (
    path: string,
    body?: unknown,
    method?: "POST" | "PUT" | "DELETE",
  ) => Promise<unknown>;
} {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const mutate = useCallback(
    async (
      path: string,
      body?: unknown,
      method: "POST" | "PUT" | "DELETE" = "POST",
    ) => {
      setSubmitting(true);
      setError(null);
      try {
        const result = await api(path, { method, body });
        setSubmitting(false);
        return result;
      } catch (cause) {
        setSubmitting(false);
        throw cause;
      }
    },
    [],
  );

  return { submitting, error, mutate };
}
