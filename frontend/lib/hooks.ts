"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "./api";

/** A page of results from the backend (matches the FastAPI `Page` schema). */
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

/** Fetch a resource on mount + whenever `path` changes. `null` path = skip. */
export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Guarda de fuera-de-orden: al paginar rápido, una respuesta vieja puede
  // resolverse DESPUÉS de la nueva; solo se aplica la última pedida.
  const seqRef = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++seqRef.current;
    if (path === null) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await apiFetch<T>(path);
      if (seq !== seqRef.current) return; // llegó tarde: ya hay otra petición
      setData(result);
    } catch (e) {
      if (seq !== seqRef.current) return;
      setError(e instanceof ApiError ? e.message : "Error al cargar");
      setData(null);
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, loading, error, reload, setData };
}

/** Imperative create/update/delete helper. Throws on error so callers can toast. */
export function useMutation() {
  const [loading, setLoading] = useState(false);

  const send = useCallback(
    async <T>(path: string, method: string, body?: unknown): Promise<T> => {
      setLoading(true);
      try {
        return await apiFetch<T>(path, {
          method,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } finally {
        setLoading(false);
      }
    },
    []
  );

  return {
    loading,
    post: <T,>(path: string, body?: unknown) => send<T>(path, "POST", body),
    patch: <T,>(path: string, body?: unknown) => send<T>(path, "PATCH", body),
    del: <T,>(path: string) => send<T>(path, "DELETE"),
  };
}
