import { CalendarClock, Trophy, Target, Gem } from "lucide-react";
import type { Summary } from "@/types";
import { StatTileSkeleton } from "./Skeleton";

// Indicadores estadisticos GLOBALES al pie del dashboard (pedido
// explicito del rediseño). Todos derivados de /predictions/summary, que
// YA existia -- nunca una metrica de ejemplo inventada aqui.
const TILES: { key: keyof Summary; label: string; icon: typeof CalendarClock }[] = [
  { key: "matches_pending", label: "Partidos pendientes", icon: CalendarClock },
  { key: "competitions_count", label: "Ligas activas", icon: Trophy },
  { key: "opportunities_count", label: "Con valor estadístico", icon: Target },
  { key: "high_quality_count", label: "Con valor y alta confianza", icon: Gem },
];

export default function StatsFooter({ summary }: { summary: Summary | null }) {
  if (!summary) {
    return (
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {TILES.map((t) => (
          <StatTileSkeleton key={t.key} />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 text-xs uppercase tracking-wide text-slate-500">
        Resumen del día ({summary.date})
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {TILES.map(({ key, label, icon: Icon }) => (
          <div key={key} className="rounded-xl border border-surface-border bg-card p-4">
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <Icon className="h-3.5 w-3.5" />
              {label}
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-100">{summary[key]}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
