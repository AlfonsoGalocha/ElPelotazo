"use client";

import { useState } from "react";
import { MARKET_DISPLAY_NAMES } from "@/types";
import type { PerformanceSegment } from "@/types";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
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
    <div className="rounded-xl border border-surface-border bg-card p-4">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-200">
          {segment.competition_code} · {MARKET_DISPLAY_NAMES[segment.market] ?? segment.market}
        </h3>
        <span className="flex items-center gap-2 text-xs text-slate-500">
          {segment.n_settled} predicciones liquidadas
          {segment.low_sample_size && (
            <span
              className="rounded border border-amber-700/50 bg-amber-900/30 px-1.5 py-0.5 text-[10px] font-medium uppercase text-amber-400"
              title="Menos de 30 predicciones liquidadas: estas metricas tienen poca evidencia estadistica, no las trates como definitivas."
            >
              Muestra pequeña
            </span>
          )}
        </span>
      </div>
      <div className="mb-3 grid grid-cols-2 gap-3 text-xs text-slate-400 sm:grid-cols-5">
        <div>
          <div className="uppercase text-slate-500">Acierto (IC 95%)</div>
          <div className="text-slate-200">
            {(segment.accuracy * 100).toFixed(1)}%{" "}
            <span className="text-slate-500">
              [{(segment.accuracy_confidence_interval_95[0] * 100).toFixed(0)}–
              {(segment.accuracy_confidence_interval_95[1] * 100).toFixed(0)}%]
            </span>
          </div>
        </div>
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
// cuales mostrar, sin volver a pedir nada a la API. Pedido real del
// usuario: "ponme el mismo filtro de oportunidades en todas las secciones
// de la app" -- un unico desplegable con optgroup Ligas/Selecciones,
// "Todas" por defecto (nunca botones sueltos de multi-seleccion).
export default function ModelPerformanceBoard({ segments }: { segments: PerformanceSegment[] }) {
  const leagueCodes = [...new Set(segments.map((s) => s.competition_code))].sort();
  const clubCodes = leagueCodes.filter((code) => !isInternationalCompetition(code));
  const internationalCodes = leagueCodes.filter((code) => isInternationalCompetition(code));
  const [selected, setSelected] = useState<string | null>(null);

  const visibleSegments = selected ? segments.filter((s) => s.competition_code === selected) : segments;

  return (
    <>
      {leagueCodes.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Liga:</span>
          <select
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value || null)}
            className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
          >
            <option value="">Todas</option>
            <optgroup label="Ligas">
              {clubCodes.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </optgroup>
            {internationalCodes.length > 0 && (
              <optgroup label="Selecciones">
                {internationalCodes.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
      )}

      {visibleSegments.length === 0 && (
        <div className="rounded-xl border border-surface-border bg-card p-6 text-sm text-slate-500">
          Todavía no hay predicciones liquidadas.
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
