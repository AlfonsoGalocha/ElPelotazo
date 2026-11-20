import { MARKET_DISPLAY_NAMES, type PerformanceSegment } from "@/types";

export default function StatsOverview({ segments }: { segments: PerformanceSegment[] }) {
  const groups = new Map<string, { n: number; quoted: number; stake: number; pnl: number }>();
  const nPredictions = segments.reduce((total, row) => total + row.n_settled, 0);
  for (const row of segments) {
    const strategy = row.market_strategy;
    if (!row.n_with_market_odds) continue;
    const name = MARKET_DISPLAY_NAMES[row.market] ?? row.market;
    const totals = groups.get(name) ?? { n: 0, quoted: 0, stake: 0, pnl: 0 };
    totals.quoted += row.n_with_market_odds;
    totals.n += strategy?.n_bets ?? 0;
    totals.stake += strategy?.total_staked ?? 0;
    totals.pnl += strategy?.total_pnl ?? 0;
    groups.set(name, totals);
  }

  return (
    <section className="rounded-xl border border-surface-border bg-card p-4">
      <h2 className="font-semibold">{nPredictions} predicciones históricas evaluadas</h2>
      <p className="my-3 text-sm text-slate-400">
        La simulación usa una unidad cuando la probabilidad del modelo supera la que implica la cuota guardada.
      </p>
      {groups.size ? <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <thead className="text-slate-400"><tr>
          <th className="p-2">Mercado</th><th className="p-2">Con cuota</th><th className="p-2">Apuestas simuladas</th><th className="p-2">Rentabilidad simulada</th>
        </tr></thead>
        <tbody>{[...groups].sort((a, b) => b[1].n - a[1].n).map(([name, row]) => (
          <tr key={name} className="border-t border-surface-border">
            <td className="p-2">{name}</td><td className="p-2">{row.quoted}</td><td className="p-2">{row.n}{row.n < 30 && <span className="ml-2 text-xs text-slate-400">Muestra pequeña</span>}</td>
            <td className="p-2">{row.stake > 0 ? `${(100 * row.pnl / row.stake).toFixed(1)} %` : "—"}</td>
          </tr>
        ))}</tbody>
      </table></div> : <p className="text-sm text-slate-400">Todavía no hay selecciones históricas evaluadas con cuota.</p>}
    </section>
  );
}
