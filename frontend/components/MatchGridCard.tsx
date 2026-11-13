import Link from "next/link";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "./SignalBadge";

// Pedido real del usuario: en vez de una lista vertical, partidos en
// "cuadraditos" dispuestos en fila (grid), para aprovechar mejor el
// espacio horizontal. Muestra solo la mejor señal del partido (misma
// logica/frase que MatchSummary.tsx) -- el desglose completo de
// mercados sigue a un clic, en la pagina del partido.
function probabilityPhrase(probability: number): string {
  const pct = Math.round(probability * 100);
  if (pct >= 70) return `muy probable (${pct}%)`;
  if (pct >= 55) return `probable (${pct}%)`;
  return `posible (${pct}%)`;
}

export default function MatchGridCard({ predictions }: { predictions: Prediction[] }) {
  if (predictions.length === 0) return null;
  const match = predictions[0].match;
  const best = predictions.find((p) => p.is_best_prediction);
  const kickoff = new Date(match.kickoff_utc);

  return (
    <Link
      href={`/matches/${match.id}`}
      className="flex flex-col gap-2 rounded-lg border border-surface-border bg-surface-raised p-3 text-sm hover:border-slate-500 hover:bg-black/20"
    >
      <div className="text-xs text-slate-500">
        {kickoff.toLocaleDateString("es-ES", { weekday: "short", day: "2-digit", month: "short" })}
        {" · "}
        {kickoff.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })}
      </div>
      <div className="font-medium leading-snug text-slate-100">
        {match.home_team.canonical_name}
        <span className="text-slate-500"> vs </span>
        {match.away_team.canonical_name}
      </div>

      {best ? (
        <div className="mt-auto flex flex-col gap-1">
          <div className="text-xs text-slate-300">
            {MARKET_DISPLAY_NAMES[best.market] ?? best.market}
            <span className="text-slate-500"> — {probabilityPhrase(best.model_probability)}</span>
          </div>
          <div className="flex items-center justify-between gap-2">
            {best.market_odds != null ? (
              <span className="text-xs text-slate-400">cuota {best.market_odds.toFixed(2)}</span>
            ) : (
              <span />
            )}
            <SignalBadge tier={best.signal_tier} />
          </div>
        </div>
      ) : (
        <div className="mt-auto text-xs text-slate-500">Sin señal clara todavía</div>
      )}
    </Link>
  );
}
