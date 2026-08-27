// Color con el que se reconoce cada empresa (lista de Ajustes › Empresas y
// switcher del Topbar). Automático por defecto: se DERIVA del id, así que una
// cuenta con varias empresas ya viene con colores distintos sin que nadie los
// elija. Quien administra la empresa puede fijar otro del catálogo.
//
// Espejo de COLORES_EMPRESA en backend/app/api/v1/empresa.py — el backend
// rechaza cualquier color fuera de esta lista. Si cambias una, cambia la otra.
export const COLORES_EMPRESA = [
  "#2c3e50", // azul marino (el acento de la marca)
  "#0f7b6c", // verde azulado
  "#a3431a", // terracota
  "#6b3fa0", // morado
  "#1f6feb", // azul
  "#9b1c4b", // vino
  "#3f6212", // olivo
  "#414d58", // grafito
] as const;

/** El color de una empresa: el elegido, o el automático derivado de su id. */
export function colorEmpresa(tenantId: string, elegido?: string | null): string {
  if (elegido) return elegido;
  let h = 0;
  for (let i = 0; i < tenantId.length; i++) h = (h * 31 + tenantId.charCodeAt(i)) >>> 0;
  return COLORES_EMPRESA[h % COLORES_EMPRESA.length];
}

// Sufijos societarios y conectores: "Frutas Kelly S.A. de C.V." → "FK", no "FS".
const RELLENO = new Set([
  "de", "del", "la", "el", "los", "las", "y",
  "sa", "s", "cv", "c", "rl", "sc", "sapi", "spr", "ac",
]);

/** Las dos letras que se pintan en el cuadro de color. */
export function inicialesEmpresa(nombre: string): string {
  const palabras = nombre
    .replace(/[.,]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .filter((p) => !RELLENO.has(p.toLowerCase()));
  const letras = palabras.slice(0, 2).map((p) => p[0]).join("");
  return (letras || nombre.slice(0, 2)).toUpperCase();
}
