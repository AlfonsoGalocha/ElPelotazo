import { BarChart3 } from "lucide-react";
import { getModelPerformance } from "@/lib/api";
import ModelPerformanceBoard from "@/components/ModelPerformanceBoard";
import StatsOverview from "@/components/StatsOverview";
import { EmptyState } from "@/components/EmptyState";

export default async function ModelPerformancePage() {
  const report = await getModelPerformance().catch(() => ({ segments: [] }));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
          <BarChart3 className="h-5 w-5 text-accent" />
          Estadísticas
        </h1>
        <p className="text-sm text-slate-500">
          Calculado únicamente sobre predicciones ya liquidadas (con resultado real conocido),
          segmentado por competición y mercado. Nunca es una garantía de resultados futuros: es lo
          que ha pasado hasta ahora con los datos disponibles.
        </p>
      </div>

      {report.segments.length === 0 && (
        <EmptyState
          title="Todavía no hay predicciones liquidadas"
          description="Ejecuta football-edge refresh tras jugarse partidos reales."
        />
      )}

      {report.segments.length > 0 && (
        <>
          <StatsOverview segments={report.segments} />
          <ModelPerformanceBoard segments={report.segments} />
        </>
      )}
    </div>
  );
}
