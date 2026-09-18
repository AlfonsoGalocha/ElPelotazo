import Link from "next/link";
import { getBestPredictions } from "@/lib/api";
import { MARKET_DISPLAY_NAMES, marketFamily } from "@/types";
import SignalBadge from "./SignalBadge";

const FAMILY_LABELS: Record<string, string> = {
  goals: "⚽ Goles",
  cards: "🟨 Tarjetas",
  corners: "🚩 Córners",
};

export default async function BestPredictionsWidget() {
  const predictions = await getBestPredictions(5).catch(() => []);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Las 5 mejores predicciones
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Solo predicciones con mercado real (cuota, probabilidad de mercado y edge válidos —{" "}
        <Link href="/#sin-mercado" className="underline hover:text-slate-300">
          ver aparte
        </Link>{" "}
        las que no tienen mercado todavía). Se prioriza probabilidad alta del modelo{" "}
        <span className="text-slate-400">junto con</span> una cuota mejor que la justa (edge real):
        una probabilidad altísima a una cuota irrisoria (ej. 98% a 1.02) no puntúa alto.
      </p>
      <div className="flex flex-col gap-2">
        {predictions.map((p) => (
          <Link
            key={p.id}
            href={`/matches/${p.match.id}`}
            className="flex items-center justify-between rounded border border-surface-border px-3 py-2 text-sm hover:bg-black/20"
          >
            <div>
              <div className="text-slate-200">
                {p.match.home_team.canonical_name} vs {p.match.away_team.canonical_name}
              </div>
              <div className="text-xs text-slate-500">
                {FAMILY_LABELS[marketFamily(p.market)]} · {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
                {p.bookmakers_used != null && (
                  <span> · {p.bookmakers_used} {p.bookmakers_used === 1 ? "casa" : "casas"}</span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {p.market_odds != null && (
                <span className="text-xs text-slate-400">cuota {p.market_odds.toFixed(2)}</span>
              )}
              <span className="font-semibold text-slate-100">{(p.model_probability * 100).toFixed(1)}%</span>
              <SignalBadge tier={p.signal_tier} />
            </div>
          </Link>
        ))}
        {predictions.length === 0 && (
          <div className="text-sm text-slate-500">
            Sin señales con mercado válido todavía. Ejecuta{" "}
            <code className="rounded bg-black/40 px-1 py-0.5">football-edge update-odds</code>.
          </div>
        )}
      </div>
    </div>
  );
}
