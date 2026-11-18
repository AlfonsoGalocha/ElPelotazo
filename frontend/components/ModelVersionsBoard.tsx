"use client";

import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { getModelPerformance, getModels } from "@/lib/api";
import { formatMatchDateOnly } from "@/lib/dates";
import type { ModelVersionInfo, PerformanceSegment } from "@/types";
import { EmptyState } from "./EmptyState";

// Subseccion "Versiones del modelo" de "Rendimiento del modelo" (pedido
// real del usuario: "Comparativa entre versiones y evolución de su
// rendimiento"). Nunca se recalcula nada aquí: `is_active` ya lo decidió
// `promotion_service.py::evaluate_and_promote` (comparación walk-forward
// real contra la versión anterior) en el momento de entrenar -- esta
// pantalla solo lo muestra, junto con el rendimiento REAL que cada
// versión ha tenido desde entonces (`/models/performance`, igual fuente
// que las demás subsecciones, unido aquí por `model_version_id`).
interface VersionWithPerformance {
  version: ModelVersionInfo;
  nSettled: number;
  weightedBrier: number | null;
}

function joinPerformance(versions: ModelVersionInfo[], segments: PerformanceSegment[]): VersionWithPerformance[] {
  const byVersionId = new Map<number, PerformanceSegment[]>();
  for (const segment of segments) {
    const list = byVersionId.get(segment.model_version_id) ?? [];
    list.push(segment);
    byVersionId.set(segment.model_version_id, list);
  }
  return versions.map((version) => {
    const own = byVersionId.get(version.id) ?? [];
    const nSettled = own.reduce((sum, s) => sum + s.n_settled, 0);
    const weightedBrier =
      nSettled > 0
        ? own.reduce((sum, s) => sum + s.model_quality.brier_score * s.n_settled, 0) / nSettled
        : null;
    return { version, nSettled, weightedBrier };
  });
}

export default function ModelVersionsBoard() {
  const [versions, setVersions] = useState<ModelVersionInfo[] | null>(null);
  const [segments, setSegments] = useState<PerformanceSegment[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getModels(), getModelPerformance()])
      .then(([v, report]) => {
        setVersions(v);
        setSegments(report.segments);
      })
      .catch(() => setError("No se pudo conectar con la API."));
  }, []);

  if (error) return <EmptyState title={error} />;
  if (versions === null) {
    return <div className="h-48 animate-pulse rounded-xl border border-surface-border bg-card" />;
  }
  if (versions.length === 0) {
    return (
      <EmptyState
        title="Todavía no hay ningún modelo entrenado"
        description="Ejecuta football-edge train para entrenar el primero."
      />
    );
  }

  const joined = joinPerformance(versions, segments);
  const byCompetition = new Map<string, VersionWithPerformance[]>();
  for (const row of joined) {
    const code = row.version.competition_code ?? "—";
    const list = byCompetition.get(code) ?? [];
    list.push(row);
    byCompetition.set(code, list);
  }
  const competitionCodes = [...byCompetition.keys()].sort();

  return (
    <div className="flex flex-col gap-6">
      {competitionCodes.map((code) => {
        const rows = byCompetition.get(code)!;
        return (
          <div key={code}>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">{code}</h2>
            <div className="overflow-hidden rounded-xl border border-surface-border bg-card">
              <table className="w-full text-sm">
                <thead className="bg-surface-alt/60 text-left text-[11px] uppercase text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Entrenada</th>
                    <th className="px-3 py-2">Ventana de entrenamiento</th>
                    <th className="px-3 py-2">Calibración</th>
                    <th className="px-3 py-2">Predicciones liquidadas</th>
                    <th className="px-3 py-2">Brier score real</th>
                    <th className="px-3 py-2">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(({ version, nSettled, weightedBrier }) => (
                    <tr key={version.id} className="border-t border-surface-border">
                      <td className="px-3 py-2 text-slate-300">
                        {formatMatchDateOnly(version.created_at, { day: "2-digit", month: "short", year: "numeric" })}
                      </td>
                      <td className="px-3 py-2 text-slate-400">
                        {version.training_start && version.training_end
                          ? `${version.training_start} → ${version.training_end}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-400">{version.calibration_method ?? "sin calibrar"}</td>
                      <td className="px-3 py-2 text-slate-300">{nSettled}</td>
                      <td className="px-3 py-2 text-slate-300">
                        {weightedBrier !== null ? weightedBrier.toFixed(4) : "sin liquidar aún"}
                      </td>
                      <td className="px-3 py-2">
                        {version.is_active !== false ? (
                          <span className="flex items-center gap-1 text-xs font-semibold text-edge-positive">
                            <CheckCircle2 className="h-3.5 w-3.5" /> ACTIVA
                          </span>
                        ) : (
                          <span className="text-xs text-slate-500">rechazada (no mejoró fuera de muestra)</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}
