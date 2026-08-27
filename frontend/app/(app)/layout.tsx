"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Sidebar } from "@/components/Sidebar";
import { Topbar } from "@/components/Topbar";
import { OnboardingBanner } from "@/components/OnboardingBanner";
import { EmpresaIdentidad } from "@/components/EmpresaIdentidad";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { session, me, loading, accessError, signOut } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !session) router.replace("/login");
  }, [loading, session, router]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!session) return null; // redirecting to /login

  if (accessError || !me) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 px-4 text-center">
        <p className="text-sm font-medium">Sin acceso</p>
        <p className="max-w-sm text-sm text-muted">
          {accessError ?? "Tu cuenta aún no está provisionada."} Contacta al
          operador de la plataforma.
        </p>
        <Button variant="secondary" onClick={signOut} className="mt-2">
          Cerrar sesión
        </Button>
      </div>
    );
  }

  return (
    // La franja de color de la empresa cruza TODO el ancho, menú incluido: es lo
    // primero que ve el ojo al cambiar de pestaña.
    <div className="flex h-screen flex-col">
      <EmpresaIdentidad me={me} />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar me={me} />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Topbar me={me} onSignOut={signOut} />
          <main className="flex-1 overflow-auto bg-surface p-6">
            <div className="mb-4">
              <OnboardingBanner pathname={pathname} />
            </div>
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
