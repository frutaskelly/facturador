"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, apiFetch, registrarAlMutar } from "./api";

/** A page of results from the backend (matches the FastAPI `Page` schema). */
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

// Caché módulo-scope de GETs: las 5 pantallas de POS (y varios formularios)
// piden el MISMO `/clientes?limit=1000` al montar — antes cada navegación lo
// re-descargaba entero. TTL corto para no servir catálogos rancios; cualquier
// mutación exitosa lo vacía completo (ver useMutation), y el cambio de empresa
// hace location.assign, que ya tira todo el módulo.
const CACHE_TTL_MS = 60_000;
const cacheGet = new Map<string, { data: unknown; ts: number }>();
const enVuelo = new Map<string, Promise<unknown>>();

export function invalidarCacheGet() {
  cacheGet.clear();
}
// Cualquier escritura exitosa por apiFetch (pase o no por useMutation) tira
// el caché: un alta hecha con apiFetch directo no debe servirse rancia.
registrarAlMutar(invalidarCacheGet);

function cacheFresca(path: string): unknown | undefined {
  const hit = cacheGet.get(path);
  if (hit && Date.now() - hit.ts < CACHE_TTL_MS) return hit.data;
  return undefined;
}

/** Fetch a resource on mount + whenever `path` changes. `null` path = skip.
 *
 * Los datos previos se CONSERVAN mientras carga el path nuevo (la tabla se
 * atenúa en vez de parpadear); `reload()` explícito siempre va a la red. */
export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(
    () => (path !== null ? (cacheFresca(path) as T | undefined) ?? null : null)
  );
  const [loading, setLoading] = useState(() => path !== null && cacheFresca(path) === undefined);
  const [error, setError] = useState<string | null>(null);
  // Guarda de fuera-de-orden: al paginar rápido, una respuesta vieja puede
  // resolverse DESPUÉS de la nueva; solo se aplica la última pedida.
  const seqRef = useRef(0);

  const load = useCallback(async (usarCache: boolean) => {
    const seq = ++seqRef.current;
    if (path === null) {
      setData(null);
      setLoading(false);
      return;
    }
    if (usarCache) {
      const hit = cacheFresca(path);
      if (hit !== undefined) {
        setData(hit as T);
        setError(null);
        setLoading(false);
        return;
      }
    }
    setLoading(true);
    setError(null);
    try {
      // Dedupe: si otro componente ya pidió este mismo path, comparte la ida.
      let p = usarCache ? enVuelo.get(path) : undefined;
      if (!p) {
        p = apiFetch<T>(path).finally(() => enVuelo.delete(path));
        enVuelo.set(path, p);
      }
      const result = (await p) as T;
      cacheGet.set(path, { data: result, ts: Date.now() });
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

  const reload = useCallback(() => load(false), [load]);

  useEffect(() => {
    load(true);
  }, [load]);

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

  // Estables entre renders: varias páginas los usan como dependencia de sus
  // columnas memoizadas; recrearlos por render invalidaba esos memos.
  const wrappers = useMemo(
    () => ({
      post: <T,>(path: string, body?: unknown) => send<T>(path, "POST", body),
      patch: <T,>(path: string, body?: unknown) => send<T>(path, "PATCH", body),
      put: <T,>(path: string, body?: unknown) => send<T>(path, "PUT", body),
      del: <T,>(path: string) => send<T>(path, "DELETE"),
    }),
    [send]
  );

  return { loading, ...wrappers };
}
