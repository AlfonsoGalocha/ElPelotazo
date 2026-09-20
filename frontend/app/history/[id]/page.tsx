import { notFound } from "next/navigation";
import { getHistoryDetail } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import type { HistoryPrediction } from "@/types";
import AnomalyBadges from "@/components/AnomalyBadge";

// Fase 4 (seccion 8): detalle histórico de UN partido finalizado -- TODAS
// sus predicciones (no solo la mejor) comparadas contra la realidad, para
// poder auditar por qué el modelo eligió lo que eligió.
function ResultCell({ prediction }: { prediction: HistoryPrediction }) {
  if (prediction.is_correct === null) {
    return <span className="text-xs text-slate-500">sin resolver</span>;
  }
  return prediction.is_correct ? (
    <span className="text-xs font-semibold text-emerald-400">ACERTADA ✓</span>
  ) : (
    <span className="text-xs font-semibold text-red-400">FALLADA ✕</span>
  );
}

export default async function HistoryDetailPage({ params }: { params: { id: string } }) {
  const matchId = Number(params.id);
  if (Number.isNaN(matchId)) notFound();

  const data = await getHistoryDetail(matchId).catch(() => null);
  if (!data) notFound();

  const { match, predictions, best_prediction_id } = data;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">
          {match.competition.name}
          {match.matchday != null && ` · Jornada ${match.matchday}`}
        </div>
        <h1 className="text-2xl font-semibold text-slate-100">
          {match.home_team.canonical_name} <span className="text-slate-500">vs</span>{" "}
          {match.away_team.canonical_name}
        </h1>
        <div className="text-sm text-slate-500">
          {new Date(match.kickoff_utc).toLocaleString("es-ES")} · Resultado final:{" "}
          <span className="font-semibold text-slate-200">
            {match.home_goals} - {match.away_goals}
          </span>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-surface-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Modelo</th>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Cuota</th>
              <th className="px-3 py-2">Edge</th>
              <th className="px-3 py-2">Resultado real</th>
              <th className="px-3 py-2">Estado</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p) => (
              <tr key={p.id} className="border-t border-surface-border">
                <td className="px-3 py-2 text-slate-200">
                  <div className="flex items-center gap-1.5">
                    {p.id === best_prediction_id && <span className="text-amber-400">★</span>}
                    {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
                  </div>
                  {p.anomaly_flags?.length > 0 && (
                    <div className="mt-1">
                      <AnomalyBadges flags={p.anomaly_flags} />
                    </div>
                  )}
                </td>
                <td className="px-3 py-2">{(p.model_probability * 100).toFixed(1)}%</td>
                <td className="px-3 py-2">
                  {p.market_probability !== null ? `${(p.market_probability * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="px-3 py-2">{p.market_odds !== null ? p.market_odds.toFixed(2) : "—"}</td>
                <td className="px-3 py-2">{p.edge !== null ? `${(p.edge * 100).toFixed(1)} pp` : "—"}</td>
                <td className="px-3 py-2 text-slate-300">{p.actual_result ?? "—"}</td>
                <td className="px-3 py-2">
                  <ResultCell prediction={p} />
                </td>
              </tr>
            ))}
            {predictions.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-4 text-center text-slate-500">
                  Sin predicciones guardadas para este partido.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
