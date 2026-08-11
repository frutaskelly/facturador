"use client";

// Casi-tiempo-real para las estaciones del POS: consulta GET /pos/pulse cada
// pocos segundos (barato) y dispara `onChange` solo cuando el contador subió —
// las colas se recargan al instante sin WebSocket. Sin Redis el pulso queda en
// 0 y la estación depende de su recarga periódica (degradación elegante).
import { useEffect, useRef } from "react";

import { apiFetch } from "@/lib/api";

export function usePosPulse(onChange: () => void, intervalMs = 4000) {
  const last = useRef<number | null>(null);
  const cb = useRef(onChange);
  useEffect(() => { cb.current = onChange; }, [onChange]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const { v } = await apiFetch<{ v: number }>("/api/v1/pos/pulse");
        if (!alive) return;
        if (last.current !== null && v !== last.current) cb.current();
        last.current = v;
      } catch { /* fail-open: la recarga periódica cubre */ }
    };
    const t = setInterval(tick, intervalMs);
    return () => { alive = false; clearInterval(t); };
  }, [intervalMs]);
}
