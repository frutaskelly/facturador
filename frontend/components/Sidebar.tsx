"use client";

import { useMemo } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Star } from "lucide-react";

import { Logo } from "@/components/Logo";
import { can, canAny, type Me } from "@/lib/auth";
import { useFavorites } from "@/lib/favorites";
import { NAV, type NavItem } from "@/lib/nav";

export function Sidebar({ me }: { me: Me }) {
  const pathname = usePathname();
  const { favorites, hydrated, toggle, isFavorite } = useFavorites(me.user_id);

  // Todos los items que el usuario puede ver, indexados por href.
  const itemsByHref = useMemo(() => {
    const map = new Map<string, NavItem>();
    for (const section of NAV) {
      for (const it of section.items) {
        if (can(me, it.perm) && canAny(me, it.anyPerm)) map.set(it.href, it);
      }
    }
    return map;
  }, [me]);

  // Items favoritos (en el orden guardado), descartando los ya no permitidos.
  const favoriteItems = useMemo(() => {
    if (!hydrated) return [];
    const seen = new Set<string>();
    const result: NavItem[] = [];
    for (const href of favorites) {
      if (seen.has(href)) continue;
      const it = itemsByHref.get(href);
      if (it) {
        result.push(it);
        seen.add(href);
      }
    }
    return result;
  }, [hydrated, itemsByHref, favorites]);

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-background">
      <div className="flex h-14 items-center px-5">
        <Logo size={26} wordmarkClassName="text-[15px]" />
      </div>
      <nav className="flex-1 overflow-y-auto px-3 pb-6">
        {hydrated && favoriteItems.length > 0 && (
          <div className="mt-2">
            <div className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted">
              Favoritos
            </div>
            {favoriteItems.map((it) => (
              <NavRow
                key={`fav-${it.href}`}
                item={it}
                pathname={pathname}
                favorite
                onToggle={() => toggle(it.href)}
              />
            ))}
          </div>
        )}

        {NAV.map((section) => {
          const items = section.items.filter((it) => can(me, it.perm) && canAny(me, it.anyPerm));
          if (items.length === 0) return null;
          return (
            <div key={section.section} className="mt-5 first:mt-2">
              <div className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted">
                {section.section}
              </div>
              {items.map((it) => (
                <NavRow
                  key={it.href}
                  item={it}
                  pathname={pathname}
                  favorite={hydrated && isFavorite(it.href)}
                  onToggle={() => toggle(it.href)}
                />
              ))}
            </div>
          );
        })}
      </nav>
    </aside>
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
  const active = pathname === item.href || pathname.startsWith(item.href + "/");
  const Icon = item.icon;
  return (
    <div className="group relative flex items-center">
      <Link
        href={item.href}
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
