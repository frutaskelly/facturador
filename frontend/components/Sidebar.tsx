"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { PanelLeftClose, PanelLeftOpen, Star } from "lucide-react";

import { Logo, LogoMark } from "@/components/Logo";
import { can, canAny, type Me } from "@/lib/auth";
import { useFavorites } from "@/lib/favorites";
import { useSidebarColapsado } from "@/lib/sidebar";
import { NAV, type NavItem } from "@/lib/nav";

/** ¿La ruta actual es este item? Un `startsWith` pelado marcaría `/pos` como
 *  activo estando en `/pos-algo`; hoy ninguna ruta colisiona, pero es la
 *  trampa que espera a la primera que sí. */
function esActivo(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar({ me }: { me: Me }) {
  const pathname = usePathname();
  const { colapsado, alternar } = useSidebarColapsado(me.user_id);
  const { favorites, hydrated, toggle, isFavorite } = useFavorites(me.user_id);

  /** El menú que ESTE usuario puede ver. Todo lo demás (riel, panel, favoritos,
   *  sección activa) se deriva de aquí: leer NAV crudo en cualquier punto le
   *  enseñaría a un capturista pantallas que no le tocan. */
  const navVisible = useMemo(
    () =>
      NAV.map((s) => ({ ...s, items: s.items.filter((i) => can(me, i.perm) && canAny(me, i.anyPerm)) }))
        .filter((s) => s.items.length > 0),
    [me],
  );

  /** href → {item, sección}. Resuelve los favoritos y permite decir siempre de
   *  qué sección viene un icono suelto — es lo que desambigua los repetidos
   *  (hay dos Building2 y dos Store en NAV). Se construye del menú YA filtrado,
   *  así un favorito de otra empresa o de un rol recortado no sobrevive. */
  const indice = useMemo(() => {
    const m = new Map<string, { item: NavItem; seccion: string }>();
    for (const s of navVisible) for (const it of s.items) m.set(it.href, { item: it, seccion: s.section });
    return m;
  }, [navVisible]);

  const favItems = useMemo(() => {
    if (!hydrated) return [];
    const vistos = new Set<string>();
    const out: { item: NavItem; seccion: string }[] = [];
    for (const href of favorites) {
      if (vistos.has(href)) continue;
      const hit = indice.get(href);
      if (hit) { out.push(hit); vistos.add(href); }
    }
    return out;
  }, [hydrated, favorites, indice]);

  const seccionActual = navVisible.find((s) => s.items.some((i) => esActivo(pathname, i.href)))?.section;

  // ── panel flotante de sección (sólo en modo colapsado) ──
  const [abierta, setAbierta] = useState<string | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const asideRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const disparadorRef = useRef<HTMLButtonElement | null>(null);

  // El panel es position:fixed: sin esto se queda flotando sobre la pantalla
  // nueva al navegar (botón atrás, gesto del trackpad, cambio de empresa).
  useEffect(() => { setAbierta(null); }, [pathname]);
  useEffect(() => { if (!colapsado) setAbierta(null); }, [colapsado]);

  useEffect(() => {
    if (!abierta) return;
    const cerrar = () => setAbierta(null);
    const porTecla = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setAbierta(null);
      disparadorRef.current?.focus(); // el foco vuelve a donde estaba
    };
    /** ¿El evento nació DENTRO del menú? Se pregunta por el destino en vez de
     *  confiar en stopPropagation: React engancha sus handlers en la raíz, así
     *  que un `e.stopPropagation()` en el panel corre DESPUÉS de que el evento
     *  ya pasó por este listener — y el panel se cerraba en el mousedown, antes
     *  de que el clic llegara a la estrella. Además, cuando scrollea la página
     *  `e.target` es `document`, que no es Node y hace que contains() lance.  */
    const dentro = (e: Event) => {
      const t = e.target;
      return t instanceof Node && (asideRef.current?.contains(t) || panelRef.current?.contains(t));
    };
    const porFuera = (e: Event) => { if (!dentro(e)) setAbierta(null); };
    document.addEventListener("mousedown", porFuera);
    document.addEventListener("keydown", porTecla);
    window.addEventListener("scroll", porFuera, true);
    window.addEventListener("resize", cerrar);
    return () => {
      document.removeEventListener("mousedown", porFuera);
      document.removeEventListener("keydown", porTecla);
      window.removeEventListener("scroll", porFuera, true);
      window.removeEventListener("resize", cerrar);
    };
  }, [abierta]);

  const colocar = useCallback((boton: HTMLButtonElement, seccion: string) => {
    const r = boton.getBoundingClientRect();
    const n = navVisible.find((s) => s.section === seccion)?.items.length ?? 0;
    const alto = Math.min(n * 36 + 44, window.innerHeight - 16);
    setPos({ top: Math.max(8, Math.min(r.top, window.innerHeight - alto - 8)), left: r.right + 8 });
  }, [navVisible]);

  function abrirSeccion(e: React.MouseEvent<HTMLButtonElement>, seccion: string) {
    e.stopPropagation();
    if (abierta === seccion) { setAbierta(null); return; } // segundo clic cierra
    disparadorRef.current = e.currentTarget;
    colocar(e.currentTarget, seccion);
    setAbierta(seccion);
  }

  // Marcar ⭐ desde el panel cambia el alto de la columna de favoritos y mueve
  // el botón que lo abrió: hay que reanclar o el panel queda colgando en el aire.
  useEffect(() => {
    if (abierta && disparadorRef.current) colocar(disparadorRef.current, abierta);
  }, [favItems.length, abierta, colocar]);

  // Al abrir, el foco entra al panel: así se llega a los items con teclado sin
  // tabular por todo el riel.
  useEffect(() => {
    if (abierta) panelRef.current?.querySelector<HTMLElement>("a,button")?.focus();
  }, [abierta]);

  const pie = (
    <div className="shrink-0 border-t border-border p-2">
      <button
        type="button"
        onClick={alternar}
        aria-expanded={!colapsado}
        aria-label={colapsado ? "Expandir menú" : "Contraer menú"}
        title={colapsado ? "Expandir menú" : undefined}
        className={`flex w-full items-center gap-2 rounded-lg py-2 text-sm text-muted transition hover:bg-surface-2 hover:text-foreground ${
          colapsado ? "justify-center" : "pl-2"
        }`}
      >
        {colapsado ? <PanelLeftOpen size={18} /> : <><PanelLeftClose size={18} /> Contraer menú</>}
      </button>
    </div>
  );

  // ─────────────────────────── expandido ───────────────────────────
  if (!colapsado) {
    return (
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-background">
        <div className="flex h-14 shrink-0 items-center px-5">
          <Logo size={26} wordmarkClassName="text-[15px]" />
        </div>
        <nav className="flex-1 overflow-y-auto px-3 pb-4">
          {favItems.length > 0 && (
            <div className="mt-2">
              <div className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted">Favoritos</div>
              {favItems.map(({ item }) => (
                <NavRow key={`fav-${item.href}`} item={item} pathname={pathname} favorite onToggle={() => toggle(item.href)} />
              ))}
            </div>
          )}

          {navVisible.map((section) => {
            // Lo favorito se MUDA a Favoritos (no se duplica): al quitarlo de ahí
            // vuelve solo a su sección original. Petición del dueño, 28-ago.
            const items = section.items.filter((it) => !(hydrated && isFavorite(it.href)));
            if (items.length === 0) return null;
            return (
              <div key={section.section} className="mt-5 first:mt-2">
                <div className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted">
                  {section.section}
                </div>
                {items.map((it) => (
                  <NavRow key={it.href} item={it} pathname={pathname} favorite={false} onToggle={() => toggle(it.href)} />
                ))}
              </div>
            );
          })}
        </nav>
        {pie}
      </aside>
    );
  }

  // ─────────────────────────── colapsado ───────────────────────────
  const itemsPanel = navVisible.find((s) => s.section === abierta)?.items ?? [];

  return (
    <>
      <aside ref={asideRef} className="flex w-16 shrink-0 flex-col border-r border-border bg-background">
        <div className="flex h-14 shrink-0 items-center justify-center">
          <LogoMark size={26} />
        </div>

        <nav className="flex min-h-0 flex-1 flex-col px-1.5 pb-2">
          {/* Favoritos: SIEMPRE a la vista, un icono por favorito. Tienen scroll
              propio para que las secciones de abajo no se vayan bajo el pliegue
              por muchas estrellas que se marquen. */}
          {favItems.length > 0 ? (
            <div className="min-h-0 space-y-1 overflow-y-auto">
              {favItems.map(({ item, seccion }) => {
                const Icon = item.icon;
                const activo = esActivo(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={`${seccion} · ${item.label}`}
                    aria-label={`${seccion} · ${item.label} (favorito)`}
                    aria-current={activo ? "page" : undefined}
                    className={`relative flex h-10 w-full items-center justify-center rounded-lg transition ${
                      activo ? "bg-surface-2 text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground"
                    }`}
                  >
                    {activo && <span aria-hidden className="absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-full bg-favorite" />}
                    <Icon size={18} />
                    <Star size={7} className="absolute bottom-1 right-1 fill-favorite text-favorite" aria-hidden />
                  </Link>
                );
              })}
            </div>
          ) : (
            // Sin favoritos el riel no se ve roto: se explica para qué sirve.
            <button
              type="button"
              onClick={alternar}
              title="Marca ⭐ una pantalla para fijarla aquí"
              aria-label="Aún no hay favoritos. Expande el menú para marcar uno."
              className="flex h-10 w-full shrink-0 items-center justify-center rounded-lg border border-dashed border-border text-muted transition hover:bg-surface-2"
            >
              <Star size={16} />
            </button>
          )}

          <div className="mx-2 my-2 h-px shrink-0 bg-border" />

          {/* Las secciones NO scrollean: son el mapa de la app y tienen que
              estar siempre a un clic, por muchos favoritos que haya. */}
          <div className="shrink-0">
            {navVisible.map((s) => {
              const Icon = s.icon;
              const activa = seccionActual === s.section;
              return (
                <button
                  key={s.section}
                  type="button"
                  onClick={(e) => abrirSeccion(e, s.section)}
                  aria-controls="menu-panel-seccion"
                  aria-expanded={abierta === s.section}
                  title={s.section}
                  className={`relative mb-1 flex w-full flex-col items-center gap-0.5 rounded-lg py-2 transition ${
                    activa ? "bg-surface-2 text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground"
                  }`}
                >
                  {activa && <span aria-hidden className="absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-full bg-favorite" />}
                  <Icon size={19} />
                  <span className="text-[10px] leading-tight">{s.corto}</span>
                </button>
              );
            })}
          </div>
        </nav>

        {pie}
      </aside>

      {abierta && (
        <div
          ref={panelRef}
          id="menu-panel-seccion"
          aria-label={`Sección ${abierta}`}
          style={{ top: pos.top, left: pos.left, maxHeight: "calc(100vh - 16px)" }}
          // Si el foco sale del panel con Tab se cierra: si no, quedaba abierto
          // encima del contenido con aria-expanded diciendo que sigue ahí.
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setAbierta(null); }}
          className="fixed z-50 flex w-60 flex-col overflow-hidden rounded-xl border border-border bg-background py-1 shadow-xl"
        >
          <div className="px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-muted">{abierta}</div>
          <div className="overflow-y-auto">
            {itemsPanel.map((it) => {
              const Icon = it.icon;
              const activo = esActivo(pathname, it.href);
              const fav = hydrated && isFavorite(it.href);
              return (
                <div key={it.href} className="group relative flex items-center">
                  <Link
                    href={it.href}
                    onClick={() => setAbierta(null)}
                    aria-current={activo ? "page" : undefined}
                    className={`flex flex-1 items-center gap-3 py-2 pl-3 pr-9 text-sm transition ${
                      activo ? "bg-surface-2 font-medium text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground"
                    }`}
                  >
                    <Icon size={17} />
                    {it.label}
                  </Link>
                  {/* La estrella vive aquí también: sin esto no habría manera de
                      fijar ni quitar un favorito sin expandir el menú. */}
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); toggle(it.href); }}
                    aria-pressed={fav}
                    aria-label={fav ? `Quitar ${it.label} de favoritos` : `Agregar ${it.label} a favoritos`}
                    className="absolute right-1 grid h-7 w-7 place-items-center rounded-md transition hover:bg-surface-2"
                  >
                    <Star size={15} className={fav ? "fill-favorite text-favorite" : "text-muted opacity-50 group-hover:opacity-100"} />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}

function NavRow({
  item,
  pathname,
  favorite,
  onToggle,
}: {
  item: NavItem;
  pathname: string;
  favorite: boolean;
  onToggle: () => void;
}) {
  const active = esActivo(pathname, item.href);
  const Icon = item.icon;
  return (
    <div className="group relative flex items-center">
      <Link
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={`flex flex-1 items-center gap-3 rounded-lg py-2 pl-2 pr-9 text-sm transition ${
          active
            ? "bg-surface-2 font-medium text-foreground"
            : "text-muted hover:bg-surface-2 hover:text-foreground"
        }`}
      >
        <Icon size={18} />
        {item.label}
      </Link>
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={favorite}
        aria-label={favorite ? `Quitar ${item.label} de favoritos` : `Agregar ${item.label} a favoritos`}
        title={favorite ? "Quitar de favoritos" : "Agregar a favoritos"}
        className="absolute right-1 grid h-7 w-7 place-items-center rounded-md transition hover:bg-surface-2"
      >
        <Star
          size={15}
          className={
            favorite
              ? "fill-favorite text-favorite"
              : "text-muted opacity-50 transition group-hover:opacity-100 hover:text-foreground"
          }
        />
      </button>
    </div>
  );
}
