import { getTopSignals } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";

export default async function TopSignalsPage({
  searchParams,
}: {
  searchParams: { date?: string; sort_by?: "score" | "edge" };
}) {
  const { date } = searchParams;
  const sortBy = searchParams.sort_by === "edge" ? "edge" : "score";
  const predictions = await getTopSignals(30, date, sortBy).catch(() => []);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Mejores señales</h1>
      <p className="mb-4 text-sm text-slate-500">
        {date
          ? `Solo partidos del ${new Date(`${date}T00:00:00`).toLocaleDateString("es-ES", {
              weekday: "long",
              day: "2-digit",
              month: "short",
            })}.`
          : "De los próximos 4 días."}{" "}
        Solo predicciones con mercado real y válido (cuota &gt; 1.0, edge no
        negativo, al menos una casa de apuestas respaldando el consenso).{" "}
        {sortBy === "edge"
          ? "Ordenado por edge de mayor a menor."
          : "Puntuación = probabilidad²× edge × confianza × calidad de datos — una cuota irrisoria (ej. 1.02) con edge casi nulo no sube aquí aunque la probabilidad del modelo sea altísima. La confianza ya incluye cuántas casas respaldan la cuota: un consenso de una única casa pesa menos que el de 5+."}
      </p>

      <form className="mb-6 flex flex-wrap items-center gap-2 text-sm text-slate-400">
        <label htmlFor="date">Filtrar por día:</label>
        <input
          id="date"
          type="date"
          name="date"
          defaultValue={date ?? ""}
          className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        />
        <label htmlFor="sort_by" className="ml-2">
          Ordenar por:
        </label>
        <select
          id="sort_by"
          name="sort_by"
          defaultValue={sortBy}
          className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        >
          <option value="score">Puntuación (por defecto)</option>
          <option value="edge">Edge (mayor a menor)</option>
        </select>
        <button type="submit" className="rounded border border-surface-border px-3 py-1 hover:bg-surface-raised">
          Aplicar
        </button>
        {date && (
          <a
            href={`/top-signals${sortBy === "edge" ? "?sort_by=edge" : ""}`}
            className="text-slate-500 underline hover:text-slate-300"
          >
            Quitar filtro de fecha
          </a>
        )}
      </form>

      <div className="overflow-hidden rounded-lg border border-surface-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Partido</th>
              <th className="px-3 py-2">Fecha</th>
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
                <td className="px-3 py-2 text-slate-400">
                  {new Date(p.match.kickoff_utc).toLocaleDateString("es-ES", {
                    weekday: "short",
                    day: "2-digit",
                    month: "short",
                  })}
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
