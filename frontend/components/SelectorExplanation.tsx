import type { SelectorDecisionsForMatch } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { formatMatchDateTime } from "@/lib/dates";
import { exclusionLabel, RELIABILITY_SOURCE_LABELS } from "@/lib/predictionLabels";
import CategoryBadge from "@/components/CategoryBadge";
import ContradictionBadge from "@/components/ContradictionBadge";

// v2.8.3 (seccion 3 del pedido, "explicacion de la seleccion"): lee el
// corte MAS RECIENTE que `capture_selector_decisions` ya guardo para
// este partido (GET /predictions/selector-decisions/{match_id}) y lo
// muestra tal cual -- que alternativas evaluo, cual eligio, por que
// descarto las demas y que contradiccion encontro en cada una. Nunca
// inventa un motivo: una candidata sin `exclusion_reason_v3` y sin
// `is_selected_v3` solo puede ser la propia fila seleccionada (ninguna
// otra queda sin motivo registrado por el gate v3).
export default function SelectorExplanation({
  data,
  currentPredictionId,
}: {
  data: SelectorDecisionsForMatch | null;
  currentPredictionId: number;
}) {
  if (!data) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-4 text-sm text-slate-400">
        No disponible: todavía no se ha capturado un corte del selector para este partido (fuera de la ventana
        de captura del scheduler, o pendiente del próximo corte).
      </div>
    );
  }

  const candidates = [...data.candidates].sort((a, b) => {
    const rankA = a.rank_position_v3 ?? Number.MAX_SAFE_INTEGER;
    const rankB = b.rank_position_v3 ?? Number.MAX_SAFE_INTEGER;
    return rankA - rankB;
  });

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-slate-500">
        Corte del selector capturado el {formatMatchDateTime(data.captured_at)} (versión {data.selector_version}).
        Compara todas las alternativas con cuota que evaluó para este partido, no solo la elegida.
      </p>
      <div className="overflow-x-auto rounded-lg border border-surface-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Prob. calibrada</th>
              <th className="px-3 py-2">Cuota</th>
              <th className="px-3 py-2">EV conservador</th>
              <th className="px-3 py-2">Fiabilidad histórica</th>
              <th className="px-3 py-2">Categoría</th>
              <th className="px-3 py-2">Contradicción</th>
              <th className="px-3 py-2">Resultado</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => {
              const isCurrent = c.prediction_id === currentPredictionId;
              return (
                <tr
                  key={c.prediction_id}
                  className={
                    "border-t border-surface-border " +
                    (c.is_selected_v3 ? "bg-accent/5" : "") +
                    (isCurrent ? " ring-1 ring-inset ring-accent/40" : "")
                  }
                >
                  <td className="px-3 py-2 text-slate-200">
                    {MARKET_DISPLAY_NAMES[c.market] ?? c.market}
                    {isCurrent && <span className="ml-1 text-[10px] text-accent">(esta señal)</span>}
                  </td>
                  <td className="px-3 py-2 text-slate-300">
                    {c.calibrated_probability !== null ? `${(c.calibrated_probability * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-3 py-2 text-slate-300">{c.market_odds !== null ? c.market_odds.toFixed(2) : "—"}</td>
                  <td className="px-3 py-2 text-slate-300">
                    {c.ev_conservative !== null ? `${c.ev_conservative >= 0 ? "+" : ""}${(c.ev_conservative * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-3 py-2 text-slate-400">
                    {c.market_track_record_value !== null
                      ? `${(c.market_track_record_value * 100).toFixed(0)}% (${
                          RELIABILITY_SOURCE_LABELS[c.market_track_record_source_level ?? ""] ?? c.market_track_record_source_level
                        })`
                      : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <CategoryBadge category={c.category} />
                  </td>
                  <td className="px-3 py-2">
                    <ContradictionBadge level={c.contradiction_level} />
                  </td>
                  <td className="px-3 py-2">
                    {c.is_selected_v3 ? (
                      <span className="font-semibold text-emerald-400">Seleccionada</span>
                    ) : c.exclusion_reason_v3 ? (
                      <span className="text-slate-400">{exclusionLabel(c.exclusion_reason_v3)}</span>
                    ) : (
                      <span className="text-slate-500">Evaluada, no elegida</span>
                    )}
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
