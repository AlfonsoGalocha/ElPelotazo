import type { Prediction } from "@/types";
import { CARDS_MARKETS, CORNERS_MARKETS, GOALS_MARKETS, MARKET_DISPLAY_NAMES, MATCH_RESULT_MARKETS } from "@/types";

// Pedido real del usuario: "pinchar un partido que me diga las cosas que
// es lo mas probable ya sea goles corners tarjetas o ambos, que me haga
// una comparacion [modelo vs cuota real]". Un resumen en lenguaje llano
// ARRIBA del todo, con lo mas probable de cada categoria (si el partido
// tiene predicciones en ella) -- las tablas tecnicas completas con TODOS
// los mercados siguen debajo, nada se quita.
const GROUPS: { label: string; markets: string[] }[] = [
  { label: "Resultado", markets: MATCH_RESULT_MARKETS },
  { label: "Goles", markets: GOALS_MARKETS },
  { label: "Tarjetas", markets: CARDS_MARKETS },
  { label: "Córners", markets: CORNERS_MARKETS },
];

function pickMostLikely(predictions: Prediction[]): Prediction | null {
  if (predictions.length === 0) return null;
  const withMarket = predictions.filter((p) => p.has_market);
  const pool = withMarket.length > 0 ? withMarket : predictions;
  return pool.reduce((best, p) => (p.model_probability > best.model_probability ? p : best));
}

export default function MatchTopPicks({ predictions }: { predictions: Prediction[] }) {
  const picks = GROUPS.map((group) => ({
    label: group.label,
    pick: pickMostLikely(predictions.filter((p) => group.markets.includes(p.market))),
  })).filter((g) => g.pick !== null);

  if (picks.length === 0) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-4 text-sm text-slate-500">
        Sin predicciones para este partido todavía.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {picks.map(({ label, pick }) => {
        const p = pick!;
        const pct = Math.round(p.model_probability * 100);
        return (
          <div key={label} className="rounded-lg border border-surface-border bg-surface-raised p-4">
            <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
            <div className="mt-1 text-sm font-medium text-slate-100">
              {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
            </div>
            <div className="mt-1 text-2xl font-semibold text-slate-100">{pct}%</div>
            {p.market_odds !== null ? (
              <div className="mt-1 text-xs text-slate-400">
                Casas: cuota {p.market_odds.toFixed(2)}
                {p.market_probability !== null && ` (${(p.market_probability * 100).toFixed(1)}%)`}
              </div>
            ) : (
              <div className="mt-1 text-xs text-slate-500">Sin cuota de mercado todavía</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
