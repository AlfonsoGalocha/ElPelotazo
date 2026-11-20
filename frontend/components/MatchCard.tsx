import Link from "next/link";
import { ArrowRight, Gauge } from "lucide-react";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { formatMatchDateOnly, formatMatchTimeOnly } from "@/lib/dates";
import TeamLogo from "./TeamLogo";
import CompetitionBadge from "./CompetitionBadge";
import SignalBadge from "./SignalBadge";
import MarketQualityBadge from "./MarketQualityBadge";
import AnomalyBadges from "./AnomalyBadge";

// Tarjeta de partido del rediseño ("Mejores oportunidades", cambio de
// filosofia del ranking): escudos de ambos equipos, competicion+jornada,
// fecha/hora, mercado recomendado, PROBABILIDAD CALIBRADA (protagonista,
// no el edge), cuota real, EV conservador, confianza, y acceso al
// analisis completo. El edge ya NO es el criterio destacado -- queda
// como dato secundario/informativo (ver prediction/ranking.py: el orden
// de "Mejores oportunidades" prioriza probabilidad calibrada y EV
// conservador; el edge nunca decide que se muestra como recomendado).
export default function MatchCard({ prediction }: { prediction: Prediction }) {
  const match = prediction.match;
  const assessment = prediction.opportunity;
  const price = assessment ? assessment.quote_price : prediction.market_odds;
  const ev = assessment ? assessment.ev : prediction.ev_conservative;
  const edge = assessment ? assessment.edge : prediction.edge;
  const evPositive = ev !== null && ev > 0;
  const calibratedProbability = prediction.calibrated_probability ?? prediction.model_probability;

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
          <span className="break-words text-sm font-medium text-slate-100">{match.home_team.canonical_name}</span>
        </div>
        <span className="shrink-0 px-1 text-xs font-medium text-slate-600">vs</span>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2.5 text-right">
          <span className="break-words text-sm font-medium text-slate-100">{match.away_team.canonical_name}</span>
          <TeamLogo name={match.away_team.canonical_name} logoUrl={match.away_team.logo_url} size="md" />
        </div>
      </div>

      <div className="grid gap-2 rounded-lg bg-surface-alt/60 px-3 py-2.5">
        <div className="min-w-0">
          <div className="break-words text-sm font-medium text-slate-100">
            {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-lg font-bold text-slate-100">
            <Gauge className="h-3.5 w-3.5 text-slate-500" />
            <span className="text-xs font-normal text-slate-400">Modelo</span>
            {(calibratedProbability * 100).toFixed(1)}%
            {prediction.calibrated_probability === null && (
              <span className="text-xs font-normal text-slate-500">(sin calibrar)</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          {price !== null ? (
            <div className="text-sm font-semibold text-slate-100">cuota {price.toFixed(2)} {assessment?.bookmaker ?? (assessment ? "· referencia" : "")}</div>
          ) : (
            <div className="text-xs text-slate-500">sin cuota</div>
          )}
          {prediction.market_probability !== null && (
            <div className="text-xs text-slate-300">
              Mercado {(prediction.market_probability * 100).toFixed(1)}%
              {prediction.market_probability_source?.endsWith("_no_vig") ? " sin margen" : ""}
            </div>
          )}
          {prediction.calibrated_probability !== null && ev !== null && (
            <div
              className={
                "text-sm font-bold " + (evPositive && prediction.has_statistical_value ? "text-edge-positive" : "text-slate-500")
              }
            >
              {evPositive ? "+" : ""}
              {(ev * 100).toFixed(1)}% EV
            </div>
          )}
          {edge !== null && (
            <div className="text-xs text-slate-600">
              edge {edge >= 0 ? "+" : ""}
              {(edge * 100).toFixed(1)}pp
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {prediction.has_statistical_value === undefined ? <SignalBadge tier={prediction.signal_tier} /> : (
            <span className="text-xs text-slate-300">{prediction.has_statistical_value ? (assessment?.high_confidence ? "Alta confianza" : "Valor estimado") : "Selección disponible"}</span>
          )}
          <MarketQualityBadge quality={prediction.market_quality} />
          <AnomalyBadges flags={prediction.anomaly_flags} />
        </div>
        <span className="flex items-center gap-1 text-xs font-medium text-accent opacity-0 transition-opacity group-hover:opacity-100">
          Análisis completo
          <ArrowRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </Link>
  );
}
