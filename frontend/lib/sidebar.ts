"use client";

import { useCallback, useState } from "react";

/** ¿El menú lateral está contraído? Se recuerda por usuario, mismo molde que
 *  `lib/favorites.ts` (localStorage, a prueba de storage corrupto).
 *
 *  A diferencia de los favoritos, esto se lee ANTES del primer pintado y no en
 *  un efecto: si no, el menú se vería 240 px un instante y saltaría a 64 px en
 *  cada recarga. Es seguro porque `app/(app)/layout.tsx` devuelve un <Spinner/>
 *  mientras carga la sesión, así que el Sidebar nunca se pinta en el servidor.
 *  Si algún día se quita esa puerta, esto hay que volverlo a revisar. */
function clave(userId: string) {
  return `nav:colapsado:${userId}`;
}

export function useSidebarColapsado(userId: string) {
  const [colapsado, setColapsado] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return localStorage.getItem(clave(userId)) === "1";
    } catch {
      return false; // por defecto abierto: a nadie se le contrae el menú sin pedirlo
    }
  });

  const alternar = useCallback(() => {
    setColapsado((v) => {
      const siguiente = !v;
      try {
        localStorage.setItem(clave(userId), siguiente ? "1" : "0");
      } catch {
        /* noop */
      }
      return siguiente;
    });
  }, [userId]);

  return { colapsado, alternar };
}
