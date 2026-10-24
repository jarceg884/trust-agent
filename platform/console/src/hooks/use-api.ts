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
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api-client";

export interface UseApiState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  refetch: () => void;
}

interface FetchState<T> {
  key: string;
  status: "done" | "error";
  data: T | null;
  error: ApiError | null;
}

export function useApi<T>(path: string | null): UseApiState<T> {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<FetchState<T>>({
    key: "",
    status: "done",
    data: null,
    error: null,
  });
  const mounted = useRef(true);
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
        setState({ key, status: "done", data: payload, error: null });
      })
      .catch((cause) => {
        if (!mounted.current || controller.signal.aborted) return;
        setState({
          key,
          status: "error",
          data: null,
          error:
            cause instanceof ApiError
              ? cause
              : new ApiError(0, "unknown", String(cause), null),
        });
      });
    return () => controller.abort();
  }, [path, key]);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  // Derivación: la request en curso (key) aún sin resultado → loading.
  if (path === null) {
    return { data: null, loading: false, error: null, refetch };
  }
  if (state.key !== key) {
    return { data: null, loading: true, error: null, refetch };
  }
  return {
    data: state.status === "done" ? state.data : null,
    loading: false,
    error: state.status === "error" ? state.error : null,
    refetch,
  };
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
