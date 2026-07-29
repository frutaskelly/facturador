"use client";

import { useEffect } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/Button";

/**
 * Error boundary raíz (app/error.tsx): atrapa cualquier excepción de render
 * bajo el layout raíz y muestra una pantalla amable en lugar de la genérica
 * de Next.
 */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Deja rastro en consola para diagnóstico; al usuario se le muestra un
    // mensaje amable sin detalles técnicos.
    console.error(error);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-background p-8 text-center shadow-sm">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-red-50 text-danger">
          <AlertTriangle size={24} />
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-tight">Algo salió mal</h1>
        <p className="mt-2 text-sm text-muted">
          Ocurrió un error inesperado. Puedes reintentar; si el problema persiste,
          recarga la página.
        </p>
        {error.digest && (
          <p className="mt-2 text-xs text-muted">Código de referencia: {error.digest}</p>
        )}
        <Button onClick={reset} className="mt-6 w-full">
          <RotateCcw size={16} /> Reintentar
        </Button>
      </div>
    </main>
  );
}
