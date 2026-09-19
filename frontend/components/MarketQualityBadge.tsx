const STYLES: Record<string, string> = {
  HIGH: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  MEDIUM: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  LOW: "bg-rose-500/15 text-rose-400 border-rose-500/30",
};

const LABELS: Record<string, string> = {
  HIGH: "MERCADO ALTA CALIDAD",
  MEDIUM: "MERCADO MEDIA CALIDAD",
  LOW: "MERCADO BAJA CALIDAD",
};

// Calidad del MERCADO (cuantas casas respaldan la cuota + su dispersion,
// ver backend/app/prediction/market_quality.py), distinta a proposito de
// SignalBadge (calidad del MODELO/confianza): una senhal respaldada por 1
// sola casa debe distinguirse claramente de una respaldada por 20+, sin
// que eso quede escondido dentro de un unico numero de "confianza".
export default function MarketQualityBadge({ quality }: { quality: "HIGH" | "MEDIUM" | "LOW" | null }) {
  if (quality === null) return <span className="text-xs text-slate-500">—</span>;
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-[10px] font-medium tracking-wide ${STYLES[quality]}`}
    >
      {LABELS[quality]}
    </span>
  );
}
