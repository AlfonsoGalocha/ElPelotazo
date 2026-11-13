"use client";

import { useState } from "react";
import { MARKET_DISPLAY_NAMES } from "@/types";
import type { PerformanceSegment } from "@/types";
import CalibrationChart from "@/components/CalibrationChart";
import EdgeRoiChart from "@/components/EdgeRoiChart";

// Fase 5/6 (seccion 10/11/12): rendimiento REAL del modelo, calculado SOLO
// sobre predicciones ya liquidadas (nunca un backtest sintetico). Fase 6
// sustituye las barras de progreso en CSS por graficos Recharts
// interactivos (CalibrationChart/EdgeRoiChart) -- misma paleta que el
// resto del tablero (colores solo transmiten estado).

function SegmentCard({ segment }: { segment: PerformanceSegment }) {
  const strategy = segment.market_strategy;
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-200">
          {segment.competition_code} · {MARKET_DISPLAY_NAMES[segment.market] ?? segment.market}
        </h3>
        <span className="text-xs text-slate-500">{segment.n_settled} predicciones liquidadas</span>
      </div>
      <div className="mb-3 grid grid-cols-2 gap-3 text-xs text-slate-400 sm:grid-cols-4">
        <div>
          <div className="uppercase text-slate-500">Brier score</div>
          <div className="text-slate-200">{segment.model_quality.brier_score.toFixed(3)}</div>
        </div>
        <div>
          <div className="uppercase text-slate-500">Log loss</div>
          <div className="text-slate-200">{segment.model_quality.log_loss.toFixed(3)}</div>
        </div>
        <div>
          <div className="uppercase text-slate-500">Error de calibración</div>
          <div className="text-slate-200">
            {(segment.model_quality.expected_calibration_error * 100).toFixed(1)}%
          </div>
        </div>
        <div>
          <div className="uppercase text-slate-500">ROI hipotético</div>
          <div className={strategy && (strategy.roi ?? 0) >= 0 ? "text-edge-positive" : "text-edge-negative"}>
            {strategy?.roi != null ? `${(strategy.roi * 100).toFixed(1)}% (${strategy.n_bets} apuestas)` : "sin cuota"}
          </div>
        </div>
      </div>

      {segment.performance_by_probability_bucket.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
            Calibración: probabilidad predicha vs frecuencia real
          </div>
          <CalibrationChart buckets={segment.performance_by_probability_bucket} />
        </div>
      )}

      {segment.performance_by_edge_bucket.length > 0 && (
        <div>
          <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">
            Resultado real por rango de edge (nunca asumas que más edge = mejor)
          </div>
          <EdgeRoiChart buckets={segment.performance_by_edge_bucket} />
        </div>
      )}
    </div>
  );
}

// Filtro por liga (pedido por el usuario): todos los segmentos (de todas
// las ligas) ya se traen server-side (page.tsx) -- aqui solo se decide
// cuales mostrar, sin volver a pedir nada a la API. De SELECCION, no de
// exclusion (pedido explicito: "tengo que deseleccionar" era el problema):
// empieza sin ninguna marcada, el usuario elige que liga(s) quiere ver.
export default function ModelPerformanceBoard({ segments }: { segments: PerformanceSegment[] }) {
  const leagueCodes = [...new Set(segments.map((s) => s.competition_code))].sort();
  const [selected, setSelected] = useState<string[]>([]);

  function toggle(code: string) {
    setSelected((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));
  }

  const allSelected = selected.length === leagueCodes.length;
  const visibleSegments = segments.filter((s) => selected.includes(s.competition_code));

  return (
    <>
      {leagueCodes.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Ligas:</span>
          <button
            onClick={() => setSelected(leagueCodes)}
            className={
              "rounded-full border px-3 py-1 " +
              (allSelected
                ? "border-slate-400 bg-slate-700 text-slate-100"
                : "border-surface-border text-slate-400 hover:bg-surface-raised")
            }
          >
            Todas
          </button>
          {leagueCodes.map((code) => (
            <button
              key={code}
              onClick={() => toggle(code)}
              className={
                "rounded-full border px-3 py-1 " +
                (selected.includes(code)
                  ? "border-slate-400 bg-slate-700 text-slate-100"
                  : "border-surface-border text-slate-500 hover:bg-surface-raised")
              }
            >
              {code}
            </button>
          ))}
        </div>
      )}

      {visibleSegments.length === 0 && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-6 text-sm text-slate-500">
          {segments.length === 0
            ? "Todavía no hay predicciones liquidadas."
            : "Elige una o varias ligas arriba para ver su rendimiento."}
        </div>
      )}

      <div className="flex flex-col gap-4">
        {visibleSegments.map((segment) => (
          <SegmentCard
            key={`${segment.competition_code}-${segment.market}-${segment.model_version_id}`}
            segment={segment}
          />
        ))}
      </div>
    </>
  );
}
