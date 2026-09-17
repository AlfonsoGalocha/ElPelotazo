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

export default function PredictionRow({ prediction }: { prediction: Prediction }) {
  const edgeColor =
    prediction.edge === null
      ? "text-slate-400"
      : prediction.edge > 0
        ? "text-edge-positive"
        : "text-edge-negative";

  return (
    <div className="grid grid-cols-6 items-center gap-2 border-b border-surface-border py-2 text-sm last:border-0">
      <div className="col-span-2 font-medium text-slate-200">
        {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
      </div>
      <div className="text-slate-300">
        <div className="text-[10px] uppercase text-slate-500">Model</div>
        {pct(prediction.model_probability)}
      </div>
      <div className="text-slate-300">
        <div className="text-[10px] uppercase text-slate-500">Market</div>
        {pct(prediction.market_probability)}
      </div>
      <div className={edgeColor}>
        <div className="text-[10px] uppercase text-slate-500">Edge</div>
        {pp(prediction.edge)}
      </div>
      <div className="flex flex-col items-start gap-1">
        <span className="text-slate-300">Fair {prediction.fair_odds.toFixed(2)}</span>
        <SignalBadge tier={prediction.signal_tier} />
      </div>
    </div>
  );
}
