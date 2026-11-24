"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  CalendarDays,
  TrendingUp,
  History,
  Wallet,
  Bot,
  BarChart3,
  Settings,
  Menu,
  X,
  Circle,
  LineChart,
  ChevronDown,
} from "lucide-react";
import { pingHealth } from "@/lib/api";
import SearchBar from "@/components/SearchBar";

const NAV_ITEMS = [
  { href: "/", label: "Hoy", icon: LayoutDashboard, related: ["/top-signals", "/signals"] },
  { href: "/matches", label: "Partidos", icon: CalendarDays, related: ["/selecciones"] },
  { href: "/opportunity-performance", label: "Rendimiento", icon: TrendingUp, related: [] },
  { href: "/history", label: "Histórico", icon: History, related: ["/prediction-history"] },
  { href: "/bot-bankroll", label: "Banca del bot", icon: Wallet, related: [] },
];

const MORE_ITEMS = [
  { href: "/model", label: "Laboratorio", icon: BarChart3, related: [] },
  { href: "/ai-analyst", label: "Analista IA", icon: Bot, related: [] },
  { href: "/settings", label: "Configuración", icon: Settings, related: [] },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

// Pedido real del usuario: "indicador de estado de conexion y ultima
// actualizacion" -- un ping real a /health (nunca un estado fijo "online"
// decorativo), re-comprobado cada 30s. Nunca se inventa una hora de
// "ultima actualizacion": el backend no expone ese dato todavia, asi que
// solo se muestra cuando SI hay conexion real, sin fecha/hora adjunta.
function ConnectionStatus() {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    function check() {
      pingHealth().then((ok) => {
        if (!cancelled) setOnline(ok);
      });
    }
    check();
    const interval = setInterval(check, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="flex items-center gap-2 rounded-lg border border-surface-border bg-surface-alt/60 px-3 py-2.5 text-xs">
      <Circle
        className={
          "h-2 w-2 shrink-0 " +
          (online === null
            ? "fill-slate-500 text-slate-500"
            : online
              ? "fill-edge-positive text-edge-positive"
              : "fill-red-500 text-red-500")
        }
      />
      <span className="text-slate-400">
        {online === null ? "Comprobando conexión…" : online ? "Backend conectado" : "Sin conexión con el backend"}
      </span>
    </div>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  function navLink(item: (typeof NAV_ITEMS)[number]) {
    const active = [item.href, ...item.related].some((href) => isActive(pathname, href));
    const Icon = item.icon;
    return (
      <Link
        key={item.href}
        href={item.href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={
          "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors " +
          (active
            ? "bg-accent/15 text-slate-100 shadow-[inset_2px_0_0_0_theme(colors.accent.DEFAULT)]"
            : "text-slate-400 hover:bg-surface-alt/60 hover:text-slate-200")
        }
      >
        <Icon className={"h-4 w-4 shrink-0 " + (active ? "text-accent" : "text-slate-500")} />
        {item.label}
      </Link>
    );
  }

  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto px-4 py-6">
      <Link href="/" onClick={onNavigate} className="flex items-center gap-2.5 px-1">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15 text-accent">
          <LineChart className="h-5 w-5" />
        </span>
        <span className="text-sm font-bold leading-tight tracking-tight text-slate-100">
          FOOTBALL
          <br />
          EDGE DETECTOR
        </span>
      </Link>

      <div className="px-1">
        <SearchBar />
      </div>

      <nav aria-label="Navegación principal" className="flex flex-col gap-1">
        {NAV_ITEMS.map(navLink)}
        <details className="mt-2 border-t border-surface-border pt-2" open={MORE_ITEMS.some((item) => isActive(pathname, item.href)) || undefined}>
          <summary className="flex cursor-pointer list-none items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-slate-400 hover:text-slate-200">
            <ChevronDown className="h-4 w-4" />
            Más
          </summary>
          <div className="mt-1 pl-3">{MORE_ITEMS.map(navLink)}</div>
        </details>
      </nav>

      <div className="mt-auto">
        <ConnectionStatus />
      </div>
    </div>
  );
}

export default function Sidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const pathname = usePathname();

  // Cierra el drawer movil automaticamente al navegar (cambio de ruta).
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <>
      {/* Barra superior SOLO en movil/tablet: logo + boton de menu. En
          escritorio la sidebar ya esta siempre visible, esta barra no
          existe (ver "hidden lg:hidden" implicito por tamaño). */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-surface-border bg-surface/95 px-4 py-3 backdrop-blur lg:hidden">
        <Link href="/" className="flex items-center gap-2 text-sm font-bold text-slate-100">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <LineChart className="h-4 w-4" />
          </span>
          FOOTBALL EDGE DETECTOR
        </Link>
        <button
          onClick={() => setMobileOpen(true)}
          aria-label="Abrir menú"
          className="rounded-lg border border-surface-border p-2 text-slate-300 hover:bg-surface-alt/60"
        >
          <Menu className="h-5 w-5" />
        </button>
      </header>

      {/* Sidebar fija de escritorio. */}
      <aside className="hidden w-64 shrink-0 border-r border-surface-border bg-surface-raised lg:fixed lg:inset-y-0 lg:left-0 lg:block">
        <SidebarContent />
      </aside>

      {/* Drawer movil: overlay + panel deslizante, solo si esta abierto. */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 border-r border-surface-border bg-surface-raised shadow-2xl">
            <div className="flex justify-end px-3 pt-3">
              <button
                onClick={() => setMobileOpen(false)}
                aria-label="Cerrar menú"
                className="rounded-lg p-2 text-slate-400 hover:bg-surface-alt/60"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
    </>
  );
}
