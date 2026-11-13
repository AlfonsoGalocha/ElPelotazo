"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import SearchBar from "@/components/SearchBar";

// Pedido explicito de rediseño: menu de 6 secciones con nombres claros,
// mapeadas a paginas que YA EXISTIAN bajo otro nombre (nunca se duplica
// funcionalidad) -- Inicio->Dashboard, top-signals->Oportunidades,
// jarvis->ai-analyst (Analista IA), model->Estadisticas. "Partidos" y
// "Configuracion" son las dos unicas paginas genuinamente nuevas.
const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/matches", label: "Partidos" },
  { href: "/top-signals", label: "Oportunidades" },
  { href: "/ai-analyst", label: "Analista IA" },
  { href: "/model", label: "Estadísticas" },
  { href: "/settings", label: "Configuración" },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function NavBar() {
  const pathname = usePathname();

  return (
    <header className="border-b border-surface-border bg-surface-raised">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4">
        <Link href="/" className="text-lg font-bold tracking-tight text-slate-100">
          FOOTBALL EDGE DETECTOR
        </Link>
        <SearchBar />
        <nav className="flex flex-wrap items-center gap-1 text-sm">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={
                "rounded px-2.5 py-1.5 transition-colors " +
                (isActive(pathname, item.href)
                  ? "bg-slate-700/60 text-slate-100"
                  : "text-slate-400 hover:bg-surface/60 hover:text-slate-200")
              }
            >
              {item.label}
            </Link>
          ))}
        </nav>
        {/* El histórico de jornadas (/history) sigue existiendo -- enlace
            discreto, no es una seccion de primer nivel del menu pedido
            pero tampoco debe desaparecer ni quedar inalcanzable. */}
        <Link href="/history" className="text-xs text-slate-500 hover:text-slate-300">
          Histórico de jornadas →
        </Link>
      </div>
    </header>
  );
}
