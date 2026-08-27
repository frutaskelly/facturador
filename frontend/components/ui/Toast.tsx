"use client";

/**
 * Avisos de la app: TODOS en un popup modal con OK — nada de notificaciones
 * fugaces en la esquina, que pasan desapercibidas.
 *
 * La API (`toast.success/error/info`) no cambia, así que ninguna página necesita
 * tocarse. Los mensajes se encolan (FIFO) y se deduplican si llegan repetidos.
 */
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AlertCircle, CheckCircle2, Info } from "lucide-react";

type Kind = "success" | "error" | "info";
type Aviso = { kind: Kind; message: string };

type ToastApi = {
  success: (m: string) => void;
  error: (m: string) => void;
  info: (m: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const ESTILO: Record<Kind, { titulo: string; icono: ReactNode; fondo: string }> = {
  success: {
    titulo: "Listo",
    icono: <CheckCircle2 size={20} />,
    fondo: "bg-emerald-50 text-success",
  },
  error: {
    titulo: "Algo salió mal",
    icono: <AlertCircle size={20} />,
    fondo: "bg-red-50 text-danger",
  },
  info: {
    titulo: "Aviso",
    icono: <Info size={20} />,
    fondo: "bg-blue-50 text-blue-700",
  },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [cola, setCola] = useState<Aviso[]>([]);
  const okRef = useRef<HTMLButtonElement>(null);

  function push(kind: Kind, message: string) {
    setCola((q) => {
      const ultimo = q[q.length - 1];
      if (ultimo && ultimo.kind === kind && ultimo.message === message) return q;
      return [...q, { kind, message }];
    });
  }

  const api: ToastApi = {
    success: (m) => push("success", m),
    error: (m) => push("error", m),
    info: (m) => push("info", m),
  };

  function cerrar() {
    setCola((q) => q.slice(1));
  }

  // Foco al botón OK al abrir; Enter/Escape lo cierran.
  useEffect(() => {
    if (cola.length === 0) return;
    // El foco va al botón OK: Enter/Espacio lo activan de forma nativa. No se
    // cierra con clic fuera ni con Escape — solo con el botón.
    okRef.current?.focus();
  }, [cola.length]);

  const actual = cola[0];
  const estilo = actual ? ESTILO[actual.kind] : null;

  return (
    <ToastContext.Provider value={api}>
      {children}

      {actual && estilo && (
        <div
          role={actual.kind === "error" ? "alertdialog" : "dialog"}
          aria-modal="true"
          aria-label={estilo.titulo}
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4"
        >
          <div
            className="w-full max-w-md rounded-2xl border border-border bg-background p-6 shadow-xl"
          >
            <div className="flex items-start gap-3">
              <span
                className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full ${estilo.fondo}`}
                aria-hidden="true"
              >
                {estilo.icono}
              </span>
              <div className="min-w-0">
                <h2 className="text-base font-semibold tracking-tight">{estilo.titulo}</h2>
                <p className="mt-1 break-words text-sm text-muted">{actual.message}</p>
                {cola.length > 1 && (
                  <p className="mt-2 text-xs text-muted">
                    ({cola.length - 1} aviso{cola.length > 2 ? "s" : ""} más en cola)
                  </p>
                )}
              </div>
            </div>
            <div className="mt-5 flex justify-end">
              <button
                ref={okRef}
                onClick={cerrar}
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
