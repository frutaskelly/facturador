import Link from "next/link";
import { SearchX } from "lucide-react";

/** Pantalla 404 global con la estética de la app. */
export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-background p-8 text-center shadow-sm">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-surface-2 text-muted">
          <SearchX size={24} />
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-tight">Página no encontrada</h1>
        <p className="mt-2 text-sm text-muted">
          La página que buscas no existe o fue movida.
        </p>
        <Link
          href="/dashboard"
          className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white transition hover:opacity-90"
        >
          Ir al tablero
        </Link>
      </div>
    </main>
  );
}
