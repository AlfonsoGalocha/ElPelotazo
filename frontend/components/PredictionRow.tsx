import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "./SignalBadge";

function pct(value: number | null): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function pp(value: number | null): string {
  if (value === null) return "—";
  const points = value * 100;
  return `${points >= 0 ? "+" : ""}${points.toFixed(1)} pp`;
}

function odds(value: number | null): string {
  if (value === null) return "—";
  return value.toFixed(2);
}

export default function PredictionRow({ prediction }: { prediction: Prediction }) {
  const edgeColor =
    prediction.edge === null
      ? "text-slate-400"
      : prediction.edge > 0
        ? "text-edge-positive"
        : "text-edge-negative";

  const evColor =
    prediction.expected_value === null
      ? "text-slate-400"
      : prediction.expected_value > 0
        ? "text-edge-positive"
        : "text-edge-negative";

  return (
    <div className="grid grid-cols-7 items-center gap-2 border-b border-surface-border py-2 text-sm last:border-0">
      <div className="col-span-2 font-medium text-slate-200">
        {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
      </div>
      <div className="text-slate-300">
        <div className="text-[10px] uppercase text-slate-500">Modelo</div>
        {pct(prediction.model_probability)}
      </div>
      <div className="text-slate-300">
        <div className="text-[10px] uppercase text-slate-500">Mercado</div>
        {pct(prediction.market_probability)}
      </div>
      <div className="text-slate-300">
        <div className="text-[10px] uppercase text-slate-500">Cuota</div>
        {odds(prediction.market_odds)}
      </div>
      <div className={edgeColor}>
        <div className="text-[10px] uppercase text-slate-500">Edge</div>
        {pp(prediction.edge)}
      </div>
      <div className="flex flex-col items-start gap-1">
        <span className={evColor}>
          {prediction.expected_value !== null
            ? `VE ${prediction.expected_value >= 0 ? "+" : ""}${(prediction.expected_value * 100).toFixed(0)}%`
            : `Justa ${prediction.fair_odds.toFixed(2)}`}
        </span>
        <SignalBadge tier={prediction.signal_tier} />
      </div>
    </div>
  );
}
