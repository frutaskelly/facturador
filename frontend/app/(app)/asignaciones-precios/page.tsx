import { redirect } from "next/navigation";

// «Asignación de precios» se retiró del menú (1-oct-2026): la lista se escoge
// donde vive la negociación —la ficha del proyecto, el cliente dentro de su
// plaza (Sucursales y precios) o la ficha del cliente— y el simulador «¿qué
// lista le tocaría?» vive en Listas de precios. La ruta queda para que los
// enlaces guardados no den 404.
export default function AsignacionesPreciosPage() {
  redirect("/listas-precios");
}
