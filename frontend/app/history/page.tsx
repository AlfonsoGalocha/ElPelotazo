import Link from "next/link";
import { getHistory } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import type { HistoryPrediction, MatchHistory } from "@/types";

// Fase 4 (seccion 7): "Histórico" -- partidos YA FINALIZADOS
// (Match.status == "finished", nunca por fecha) con su mejor predicción,
// probabilidad del modelo, cuota y el estado ACERTADA/FALLADA/sin
// resolver todavía (liquidado por el backend, nunca inferido aquí).
function ResultBadge({ prediction }: { prediction: HistoryPrediction | undefined }) {
  if (!prediction || prediction.is_correct === null) {
    return <span className="text-xs text-slate-500">sin resolver</span>;
  }
  return prediction.is_correct ? (
    <span className="text-xs font-semibold text-emerald-400">ACERTADA ✓</span>
  ) : (
    <span className="text-xs font-semibold text-red-400">FALLADA ✕</span>
  );
}

function bestPrediction(row: MatchHistory): HistoryPrediction | undefined {
  return row.predictions.find((p) => p.id === row.best_prediction_id);
}

export default async function HistoryPage() {
  const history = await getHistory().catch(() => []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Histórico</h1>
        <p className="text-sm text-slate-500">
          Partidos ya finalizados con su mejor predicción (mayor puntuación de señal entre las
          válidas, nunca simplemente la de mayor probabilidad/edge/cuota) comparada contra el
          resultado real. Nada aquí se recalcula con criterios de hoy: es exactamente lo que el
          sistema predijo antes del pitido inicial.
        </p>
      </div>

      {history.length === 0 && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-6 text-sm text-slate-500">
          Sin partidos finalizados todavía. Ejecuta{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">football-edge refresh</code> cuando haya
          resultados reales nuevos.
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-surface-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Partido</th>
              <th className="px-3 py-2">Resultado</th>
              <th className="px-3 py-2">Mejor predicción</th>
              <th className="px-3 py-2">Modelo</th>
              <th className="px-3 py-2">Cuota</th>
              <th className="px-3 py-2">Estado</th>
            </tr>
          </thead>
          <tbody>
            {history.map((row) => {
              const best = bestPrediction(row);
              return (
                <tr key={row.match.id} className="border-t border-surface-border">
                  <td className="px-3 py-2">
                    <Link href={`/history/${row.match.id}`} className="text-slate-200 hover:underline">
                      {row.match.home_team.canonical_name} vs {row.match.away_team.canonical_name}
                    </Link>
                    <div className="text-[11px] text-slate-500">
                      {row.match.competition.name} ·{" "}
                      {new Date(row.match.kickoff_utc).toLocaleDateString("es-ES", {
                        day: "2-digit",
                        month: "short",
                        year: "numeric",
                      })}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-medium text-slate-200">
                    {row.match.home_goals} - {row.match.away_goals}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {best ? MARKET_DISPLAY_NAMES[best.market] ?? best.market : "—"}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {best ? `${(best.model_probability * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {best?.market_odds != null ? best.market_odds.toFixed(2) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <ResultBadge prediction={best} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
