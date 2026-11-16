import Link from "next/link";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "./SignalBadge";
import MarketQualityBadge from "./MarketQualityBadge";

// Pedido real de usuario: simplificar el home -- caja a lo ANCHO (una por
// fila, no un grid de cuadraditos pequeños) para cada partido destacado de
// la jornada. Muestra solo la mejor señal real del partido (nunca inventa
// una si no la hay).
export default function FeaturedMatchBox({ prediction }: { prediction: Prediction }) {
  const match = prediction.match;
  const kickoff = new Date(match.kickoff_utc);

  return (
    <Link
      href={`/matches/${match.id}`}
      className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-surface-border bg-surface-raised px-5 py-4 hover:border-slate-500 hover:bg-black/20"
    >
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">
          {match.competition.name} ·{" "}
          {kickoff.toLocaleDateString("es-ES", {
            weekday: "short",
            day: "2-digit",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </div>
        <div className="mt-1 text-lg font-semibold text-slate-100">
          {match.home_team.canonical_name}
          <span className="mx-2 text-slate-500">vs</span>
          {match.away_team.canonical_name}
        </div>
        <div className="mt-1 text-sm text-slate-300">
          {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
          <span className="text-slate-500"> · {(prediction.model_probability * 100).toFixed(1)}%</span>
        </div>
      </div>

      <div className="flex flex-col items-end gap-1.5">
        <div className="flex items-center gap-2 text-sm">
          {prediction.market_odds != null && (
            <span className="text-slate-400">cuota {prediction.market_odds.toFixed(2)}</span>
          )}
          {prediction.edge != null && (
            <span className="font-semibold text-edge-positive">
              +{(prediction.edge * 100).toFixed(1)}pp
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <MarketQualityBadge quality={prediction.market_quality} />
          <SignalBadge tier={prediction.signal_tier} />
        </div>
      </div>
    </Link>
  );
}
