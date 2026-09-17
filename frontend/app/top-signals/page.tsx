import { getTopSignals } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";

export default async function TopSignalsPage() {
  const predictions = await getTopSignals(30).catch(() => []);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Mejores señales</h1>
      <p className="mb-6 text-sm text-slate-500">
        Ranking transparente: puntuación = edge × confianza × calidad de datos. Un edge grande de un
        modelo poco calibrado o con pocos datos NO sube en este ranking (ver docs/modeling.md).
      </p>

      <div className="overflow-hidden rounded-lg border border-surface-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Partido</th>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Modelo</th>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Cuota</th>
              <th className="px-3 py-2">Edge</th>
              <th className="px-3 py-2">Cuota justa</th>
              <th className="px-3 py-2">Calidad de datos</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p) => (
              <tr key={p.id} className="border-t border-surface-border">
                <td className="px-3 py-2 text-slate-200">
                  {p.match.home_team.canonical_name} vs {p.match.away_team.canonical_name}
                </td>
                <td className="px-3 py-2 text-slate-300">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</td>
                <td className="px-3 py-2">{(p.model_probability * 100).toFixed(1)}%</td>
                <td className="px-3 py-2">
                  {p.market_probability !== null ? `${(p.market_probability * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="px-3 py-2">{p.market_odds !== null ? p.market_odds.toFixed(2) : "—"}</td>
                <td className="px-3 py-2 text-edge-positive">
                  {p.edge !== null ? `+${(p.edge * 100).toFixed(1)} pp` : "—"}
                </td>
                <td className="px-3 py-2">{p.fair_odds.toFixed(2)}</td>
                <td className="px-3 py-2">
                  <SignalBadge tier={p.signal_tier} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {predictions.length === 0 && (
          <div className="p-6 text-sm text-slate-500">No hay señales con edge positivo todavía.</div>
        )}
      </div>
    </div>
  );
}
