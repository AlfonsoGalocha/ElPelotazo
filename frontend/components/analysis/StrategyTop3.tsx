"use client";

import { useCachedData } from "@/lib/clientCache";
import { fmt, getStrategyPicks, pct, type StrategyPicks } from "@/lib/analysis";
import { MARKET_DISPLAY_NAMES } from "@/types";

const REASONS: Record<string, string> = {
  probabilidad_inferior_al_65: "probabilidad < 65 %", cuota_fuera_de_banda: "cuota fuera de 1,30–1,80",
  ev_estimado_no_positivo: "EV no positivo", cuota_desactualizada: "cuota no vigente", sin_mercado: "sin cuota",
  origen_cuota_no_verificado: "origen de cuota sin verificar", ventaja_extrema_frente_al_mercado: "discrepancia extrema con el mercado",
  ajuste_calibracion_sin_respaldo: "ajuste de calibración sin respaldo", calibracion_en_extremo: "calibración en extremo",
  mercado_contradictorio: "mercado contradictorio", partido_iniciado_o_prediccion_fuera_de_fecha: "partido iniciado",
};

/** Best three selections by our verified-opportunity rule; kept visually apart from the raw model ranking. */
export default function StrategyTop3({ matchId }: { matchId: number }) {
  const { data, error } = useCachedData<StrategyPicks>(`strategy-picks:${matchId}`, () => getStrategyPicks(matchId));
  return (
    <section className="rounded-lg border border-emerald-500/30 bg-emerald-500/[0.03] p-3 sm:p-4">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-emerald-300">Top 3 según nuestra estrategia</h3>
        {data && data.source !== "sin_datos" && (
          <span className="text-[11px] text-slate-500">
            {data.eligible_count} {data.eligible_count === 1 ? "cumple" : "cumplen"} · {data.source === "archivo_prepartido" ? "previsión guardada antes del partido" : "calculado ahora"}
          </span>
        )}
      </div>
      <p className="mb-2 text-xs text-slate-400">{data?.rule ?? "Probabilidad ≥ 65 %, cuota vigente entre 1,30 y 1,80 y EV positivo. Si cumplen menos de 3, se completa con las más probables a cuota entre 1,40 y 2,50."}</p>
      {error && <p className="text-sm text-slate-400">No se pudo calcular.</p>}
      {!data && !error && <div className="h-20 animate-pulse rounded bg-surface-alt/40" aria-label="Calculando" />}
      {data && !data.picks.length && <p className="text-sm text-slate-400">Sin previsión disponible para este partido.</p>}
      {data && data.picks.length > 0 && (
        <ol className="space-y-1.5">
          {data.picks.map((p, i) => (
            <li key={p.market} className={`rounded border px-2.5 py-1.5 text-sm ${p.eligible ? "border-emerald-500/30" : "border-surface-border opacity-80"}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span><span className="mr-1.5 text-slate-500">{i + 1}.</span><span className="font-medium">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</span></span>
                <span className="tabular-nums text-slate-300">
                  {pct(p.probability)}{p.odds != null && <> · cuota {fmt(p.odds)}</>}
                  {p.ev != null && <span className={p.ev > 0 ? " text-emerald-400" : " text-slate-400"}> · EV {p.ev > 0 ? "+" : ""}{(p.ev * 100).toFixed(1)} %</span>}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center justify-between gap-2 text-xs">
                {p.eligible ? (
                  <span className="text-emerald-400">✓ Cumple la estrategia{p.confidence_reasons.length > 0 && <span className="text-slate-400"> · con reservas de confianza</span>}</span>
                ) : (
                  <span className="text-slate-400">No cumple: {p.reasons.map(r => REASONS[r] ?? r.replaceAll("_", " ")).join(", ")}</span>
                )}
                {p.is_correct != null && (
                  <span className={p.is_correct ? "text-emerald-400" : "text-red-400"}>{p.is_correct ? "✓ Acertó" : "✗ Falló"}</span>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
