import { getTopSignals } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";

export default async function TopSignalsPage() {
  const predictions = await getTopSignals(30).catch(() => []);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Mejores señales</h1>
      <p className="mb-6 text-sm text-slate-500">
        Solo predicciones con mercado real y válido (cuota &gt; 1.0, edge no negativo, al menos una
        casa de apuestas respaldando el consenso). Puntuación = probabilidad²× edge × confianza ×
        calidad de datos — una cuota irrisoria (ej. 1.02) con edge casi nulo no sube aquí aunque la
        probabilidad del modelo sea altísima. La confianza ya incluye cuántas casas respaldan la
        cuota: un consenso de una única casa pesa menos que el de 5+.
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
              <th className="px-3 py-2">Casas</th>
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
                <td className="px-3 py-2 text-slate-400">{p.bookmakers_used ?? "—"}</td>
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
          <div className="p-6 text-sm text-slate-500">
            No hay señales con mercado válido todavía. Ejecuta{" "}
            <code className="rounded bg-black/40 px-1 py-0.5">football-edge update-odds</code>.
          </div>
        )}
      </div>
    </div>
  );
}
