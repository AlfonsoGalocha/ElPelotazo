import { getModelPerformance } from "@/lib/api";
import ModelPerformanceBoard from "@/components/ModelPerformanceBoard";

export default async function ModelPerformancePage() {
  const report = await getModelPerformance().catch(() => ({ segments: [] }));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Rendimiento del modelo</h1>
        <p className="text-sm text-slate-500">
          Calculado únicamente sobre predicciones ya liquidadas (con resultado real conocido),
          segmentado por competición y mercado. Nunca es una garantía de resultados futuros: es lo
          que ha pasado hasta ahora con los datos disponibles.
        </p>
      </div>

      {report.segments.length === 0 && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-6 text-sm text-slate-500">
          Todavía no hay predicciones liquidadas. Ejecuta{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">football-edge refresh</code> tras jugarse
          partidos reales.
        </div>
      )}

      {report.segments.length > 0 && <ModelPerformanceBoard segments={report.segments} />}
    </div>
  );
}
