import { Spinner } from "@/components/ui/Spinner";

// Skeleton de navegación: sin esto, al entrar a Facturas no se pinta NADA
// hasta que el componente cliente monta y dispara sus fetch.
export default function Loading() {
  return (
    <div className="flex justify-center py-24">
      <Spinner />
    </div>
  );
}
