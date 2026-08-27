"use client";

import Link from "next/link";
import { ArrowRight, Building2, CheckCircle2 } from "lucide-react";

import { OnboardingChecklist, useOnboarding } from "@/components/OnboardingChecklist";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { can, useAuth } from "@/lib/auth";

const WRITE = "membership:gestionar";

export default function OnboardingPage() {
  const { me } = useAuth();
  const { status } = useOnboarding();
  const canWrite = can(me, WRITE);

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageHeader
        title="Configura tu empresa"
        subtitle="Unos pasos para que puedas emitir facturas (CFDI) a tu nombre"
      />

      {status?.listo_para_facturar ? (
        <Alert tone="success" title="¡Todo listo!">
          Tu empresa ya está configurada y puedes emitir facturas. Puedes ajustar tus
          datos fiscales cuando lo necesites en Ajustes › Empresa.
        </Alert>
      ) : (
        <Alert tone="info" title="¿Cómo funciona?">
          Captura los datos fiscales de tu empresa y sube tu Certificado de Sello Digital
          (CSD) del SAT. Con eso quedas dado de alta como emisor y puedes timbrar tus
          facturas con tu propio RFC.
        </Alert>
      )}

      <OnboardingChecklist />

      {!canWrite && (
        <Alert tone="warning">
          No tienes permisos para configurar la empresa. Pide a un administrador de tu
          cuenta que complete estos pasos.
        </Alert>
      )}

      {canWrite && (
        <div className="flex items-center gap-2">
          <Link href="/ajustes/empresa/configuracion">
            <Button>
              {status?.listo_para_facturar ? (
                <>
                  <CheckCircle2 size={16} /> Ver datos de la empresa
                </>
              ) : (
                <>
                  <Building2 size={16} /> Continuar configuración <ArrowRight size={16} />
                </>
              )}
            </Button>
          </Link>
        </div>
      )}
    </div>
  );
}
