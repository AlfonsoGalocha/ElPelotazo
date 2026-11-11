import type { Prediction, SignalTier } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";

// Vista SIMPLE por defecto (seccion "demasiados numeros/terminos tecnicos"
// + "demasiadas paginas" -- feedback directo del usuario): una frase en
// lenguaje llano con la mejor señal del partido, nunca edge/fair_odds/
// intervalo de incertidumbre/anomalias (eso sigue disponible en "Ver
// todos los mercados", MatchCard.tsx). Si no hay ninguna señal que pase
// el filtro de calidad, se dice asi, nunca se fuerza una.
const CONFIDENCE_LABEL: Partial<Record<SignalTier, string>> = {
  HIGH_DATA_SUPPORT: "Confianza alta",
  LOW_DATA_SUPPORT: "Confianza baja",
};

function probabilityPhrase(probability: number): string {
  const pct = Math.round(probability * 100);
  if (pct >= 70) return `muy probable (${pct}%)`;
  if (pct >= 55) return `probable (${pct}%)`;
  return `posible (${pct}%)`;
}

export default function MatchSummary({ predictions }: { predictions: Prediction[] }) {
  const best = predictions.find((p) => p.is_best_prediction);

  if (!best) {
    return (
      <p className="text-sm text-slate-500">
        Sin una señal lo bastante clara para este partido todavía — mira "Ver todos los mercados" si quieres
        el detalle completo.
      </p>
    );
  }

  const marketName = MARKET_DISPLAY_NAMES[best.market] ?? best.market;
  const confidenceLabel = CONFIDENCE_LABEL[best.signal_tier];

  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
      <span className="font-medium text-slate-100">{marketName}</span>
      <span className="text-slate-400">— {probabilityPhrase(best.model_probability)}</span>
      {best.market_odds !== null && <span className="text-slate-500">· cuota {best.market_odds.toFixed(2)}</span>}
      {confidenceLabel && (
        <span
          className={best.signal_tier === "HIGH_DATA_SUPPORT" ? "text-emerald-400" : "text-slate-500"}
        >
          · {confidenceLabel}
        </span>
      )}
    </div>
  );
}
