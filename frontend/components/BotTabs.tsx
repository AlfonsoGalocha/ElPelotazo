"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/bot-bankroll", label: "Banca" },
  { href: "/bot-bankroll/plan", label: "Próximas decisiones" },
];

export default function BotTabs() {
  const pathname = usePathname();
  return (
    <nav aria-label="Secciones del bot" className="flex gap-1 border-b border-surface-border">
      {TABS.map(tab => {
        const active = pathname === tab.href;
        return (
          <Link key={tab.href} href={tab.href} aria-current={active ? "page" : undefined}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${active ? "border-violet-400 text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
