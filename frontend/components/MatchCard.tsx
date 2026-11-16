import Link from "next/link";
import { ArrowRight, Gauge } from "lucide-react";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { formatMatchDateOnly, formatMatchTimeOnly } from "@/lib/dates";
import TeamLogo from "./TeamLogo";
import CompetitionBadge from "./CompetitionBadge";
import SignalBadge from "./SignalBadge";
import MarketQualityBadge from "./MarketQualityBadge";

// Tarjeta de partido del rediseño (spec exacta pedida por el usuario):
// escudos de ambos equipos, competicion+jornada, fecha/hora, mercado
// recomendado, probabilidad del modelo, cuota disponible, edge%,
// confianza, y acceso al analisis completo. El edge POSITIVO destaca en
// verde (edge negativo nunca se muestra como "recomendado": ver
// backend/app/prediction/ranking.py, `signal_score` solo es > 0 con
// edge > 0 -- esta tarjeta nunca recibe una prediccion con edge <= 0 como
// "mercado recomendado", pero el color se calcula igualmente por si
// algun dia se usa para mostrar cualquier prediccion).
export default function MatchCard({ prediction }: { prediction: Prediction }) {
  const match = prediction.match;
  const edgePositive = prediction.edge !== null && prediction.edge > 0;

  return (
    <Link
      href={`/matches/${match.id}`}
      className="group flex flex-col gap-4 rounded-xl border border-surface-border bg-card p-4 shadow-card transition-all hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-ambient"
    >
      <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
        <CompetitionBadge name={match.competition.name} country={match.competition.country} />
        <span className="flex items-center gap-2">
          {match.matchday != null && <span>Jornada {match.matchday}</span>}
          <span className="text-slate-600">·</span>
          <span>
            {formatMatchDateOnly(match.kickoff_utc, { day: "2-digit", month: "short" })}{" "}
            {formatMatchTimeOnly(match.kickoff_utc, { hour: "2-digit", minute: "2-digit" })}
          </span>
        </span>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <TeamLogo name={match.home_team.canonical_name} logoUrl={match.home_team.logo_url} size="md" />
          <span className="truncate text-sm font-medium text-slate-100">{match.home_team.canonical_name}</span>
        </div>
        <span className="shrink-0 px-1 text-xs font-medium text-slate-600">vs</span>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2.5 text-right">
          <span className="truncate text-sm font-medium text-slate-100">{match.away_team.canonical_name}</span>
          <TeamLogo name={match.away_team.canonical_name} logoUrl={match.away_team.logo_url} size="md" />
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 rounded-lg bg-surface-alt/60 px-3 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-100">
            {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
          </div>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-slate-500">
            <Gauge className="h-3 w-3" />
            Modelo: {(prediction.model_probability * 100).toFixed(1)}%
          </div>
        </div>
        <div className="shrink-0 text-right">
          {prediction.market_odds !== null ? (
            <div className="text-sm font-semibold text-slate-100">cuota {prediction.market_odds.toFixed(2)}</div>
          ) : (
            <div className="text-xs text-slate-500">sin cuota</div>
          )}
          {prediction.edge !== null && (
            <div
              className={
                "text-sm font-bold " + (edgePositive ? "text-edge-positive" : "text-slate-500")
              }
            >
              {edgePositive ? "+" : ""}
              {(prediction.edge * 100).toFixed(1)}% edge
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <SignalBadge tier={prediction.signal_tier} />
          <MarketQualityBadge quality={prediction.market_quality} />
        </div>
        <span className="flex items-center gap-1 text-xs font-medium text-accent opacity-0 transition-opacity group-hover:opacity-100">
          Análisis completo
          <ArrowRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </Link>
  );
}
