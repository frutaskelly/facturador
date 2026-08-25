"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertCircle } from "lucide-react";

type ToastKind = "success" | "info";
type Toast = { id: number; kind: ToastKind; message: string };

type ToastApi = {
  success: (m: string) => void;
  error: (m: string) => void;
  info: (m: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);
let _id = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Los ERRORES no son fugaces: van a un popup modal con OK que el usuario
  // tiene que cerrar — así nunca pasan desapercibidos. Cola FIFO por si
  // llegan varios; se deduplican mensajes consecutivos idénticos.
  const [errores, setErrores] = useState<string[]>([]);
  const okRef = useRef<HTMLButtonElement>(null);

  function push(kind: ToastKind, message: string) {
    const id = ++_id;
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  }

  const api: ToastApi = {
    success: (m) => push("success", m),
    info: (m) => push("info", m),
    error: (m) =>
      setErrores((q) => (q[q.length - 1] === m ? q : [...q, m])),
  };

  function cerrarError() {
    setErrores((q) => q.slice(1));
  }

  // Foco al botón OK al abrir y cierre con Escape/Enter.
  useEffect(() => {
    if (errores.length === 0) return;
    okRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" || e.key === "Enter") {
        e.preventDefault();
        cerrarError();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [errores.length]);

  return (
    <ToastContext.Provider value={api}>
      {children}

      {/* aria-live: los avisos se anuncian a lectores de pantalla al aparecer. */}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto rounded-lg border px-4 py-2 text-sm shadow-md ${
              t.kind === "success"
                ? "border-success/30 bg-background text-success"
                : "border-border bg-background text-foreground"
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>

      {/* Popup modal de error: mensaje + OK. Bloquea hasta que el usuario lo lea. */}
      {errores.length > 0 && (
        <div
          role="alertdialog"
          aria-modal="true"
          aria-label="Error"
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4"
          onClick={cerrarError}
        >
          <div
            className="w-full max-w-md rounded-2xl border border-border bg-background p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-red-50 text-danger">
                <AlertCircle size={20} />
              </span>
              <div className="min-w-0">
                <h2 className="text-base font-semibold tracking-tight">
                  Algo salió mal
                </h2>
                <p className="mt-1 break-words text-sm text-muted">{errores[0]}</p>
                {errores.length > 1 && (
                  <p className="mt-2 text-xs text-muted">
                    ({errores.length - 1} aviso{errores.length > 2 ? "s" : ""} más en cola)
                  </p>
                )}
              </div>
            </div>
            <div className="mt-5 flex justify-end">
              <button
                ref={okRef}
                onClick={cerrarError}
                className="inline-flex items-center justify-center rounded-lg bg-accent px-6 py-2 text-sm font-medium text-white transition hover:opacity-90"
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast debe usarse dentro de <ToastProvider>");
  return ctx;
}
