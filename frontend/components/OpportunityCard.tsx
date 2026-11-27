import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import TeamLogo from "@/components/TeamLogo";
import CompetitionBadge from "@/components/CompetitionBadge";
import MarketQualityBadge from "@/components/MarketQualityBadge";
import CategoryBadge from "@/components/CategoryBadge";
import ContradictionBadge from "@/components/ContradictionBadge";
import AnomalyBadges from "@/components/AnomalyBadge";
import { formatMatchDateOnly } from "@/lib/dates";
import { exclusionLabel, RELIABILITY_SOURCE_LABELS } from "@/lib/predictionLabels";

// v2.8.3 (pulido de "Mejores oportunidades"): tarjeta compacta que
// sustituye a la fila de tabla. Mismos datos que antes (nada inventado,
// ver top-signals/page.tsx antes de este cambio) -- solo reorganizados
// para que equipo, mercado, cuota, probabilidad, EV, categoría,
// contradicción y actualidad de la cuota se lean de un vistazo, con el
// resto (fiabilidad detallada, motivos, dispersión de cuotas) detrás de
// un "Ver más" para no sobrecargar.
export default function OpportunityCard({ prediction: p }: { prediction: Prediction }) {
  const assessment = p.opportunity;
  const price = assessment?.quote_price;
  const ev = assessment?.ev;
  const conservative = assessment?.conservative_ev;
  const edge = assessment?.edge;
  const age = assessment?.quote_age_minutes;
  const reasons = (p.has_statistical_value ? assessment?.confidence_reasons : assessment?.reasons) ?? [];

  return (
    <article className="flex flex-col gap-3 rounded-xl border border-surface-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/signals/${p.id}`} className="flex items-center gap-1.5 font-medium text-slate-100 hover:underline">
            <TeamLogo name={p.match.home_team.canonical_name} logoUrl={p.match.home_team.logo_url} size="sm" />
            <span className="truncate">{p.match.home_team.canonical_name}</span>
            <span className="text-slate-500">vs</span>
            <TeamLogo name={p.match.away_team.canonical_name} logoUrl={p.match.away_team.logo_url} size="sm" />
            <span className="truncate">{p.match.away_team.canonical_name}</span>
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
            <CompetitionBadge name={p.match.competition.name} country={p.match.competition.country} />
            <span>·</span>
            <span>
              {formatMatchDateOnly(p.match.kickoff_utc, { weekday: "short", day: "2-digit", month: "short" })}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
          <MarketQualityBadge quality={p.market_quality} />
          <CategoryBadge category={p.category} categoryStatus={p.category_status} />
        </div>
      </div>

      <div className="text-sm font-semibold text-slate-200">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</div>

      <div className="grid grid-cols-3 gap-2 text-sm sm:grid-cols-4">
        <Stat
          label="Prob. calibrada"
          value={
            p.calibrated_probability !== null
              ? `${(p.calibrated_probability * 100).toFixed(1)}%`
              : `${(p.model_probability * 100).toFixed(1)}%`
          }
          hint={p.calibrated_probability === null ? "sin calibrar" : undefined}
          highlight
        />
        <Stat
          label="Cuota"
          value={price != null ? price.toFixed(2) : "—"}
          hint={assessment?.bookmaker ?? undefined}
        />
        <Stat
          label="EV estimado"
          value={ev != null ? `${ev >= 0 ? "+" : ""}${(ev * 100).toFixed(1)}%` : "—"}
          tone={ev != null && ev > 0 && p.has_statistical_value ? "positive" : undefined}
        />
        <Stat
          label="Cuota actualizada"
          value={age != null ? `hace ${Math.round(age)} min` : "sin verificar"}
          icon={p.is_stale_odds ? <AlertTriangle className="h-3 w-3 shrink-0 text-amber-400" /> : undefined}
        />
      </div>

      <div className="flex flex-wrap items-center gap-1">
        <ContradictionBadge level={p.contradiction_level} />
        <AnomalyBadges flags={p.anomaly_flags} />
      </div>

      {reasons.length > 0 && (
        <p className="text-xs text-slate-400">{reasons.map(exclusionLabel).join(" · ")}</p>
      )}

      <details className="border-t border-surface-border pt-2 text-xs text-slate-400">
        <summary className="cursor-pointer text-slate-500 hover:text-slate-300">Ver más</summary>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Detail
            label="Respaldo estadístico"
            value={
              p.reliability
                ? `${(p.reliability.value * 100).toFixed(0)}% de acierto en ${p.reliability.n_observations} liquidaciones (${RELIABILITY_SOURCE_LABELS[p.reliability.source_level]})`
                : "Sin historial liquidado suficiente"
            }
          />
          <Detail
            label="EV conservador"
            value={conservative != null ? `${conservative >= 0 ? "+" : ""}${(conservative * 100).toFixed(1)}%` : "—"}
          />
          <Detail label="Edge (informativo)" value={edge != null ? `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)} pp` : "—"} />
          <Detail
            label="Prob. de mercado"
            value={
              p.market_probability !== null
                ? `${(p.market_probability * 100).toFixed(1)}% ${p.market_probability_source?.endsWith("_no_vig") ? "(sin margen)" : "(estimada)"}`
                : "—"
            }
          />
          <Detail label="Casas usadas" value={p.bookmakers_used != null ? String(p.bookmakers_used) : "—"} />
          <Detail
            label="Equilibrio de la cuota"
            value={price != null ? `${(100 / price).toFixed(1)}%` : "—"}
          />
        </div>
      </details>
    </article>
  );
}

function Stat({
  label,
  value,
  hint,
  tone,
  highlight,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "positive";
  highlight?: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={
          "flex items-center gap-1 font-semibold " +
          (tone === "positive" ? "text-emerald-400" : highlight ? "text-slate-100" : "text-slate-200")
        }
      >
        {icon}
        {value}
      </div>
      {hint && <div className="text-[10px] text-slate-500">{hint}</div>}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-slate-300">{value}</div>
    </div>
  );
}
