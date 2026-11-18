import { BarChart3 } from "lucide-react";
import { getHistory, getModelPerformance } from "@/lib/api";
import ModelPerformanceTabs, { type Tab } from "@/components/ModelPerformanceTabs";

const VALID_TABS: Tab[] = ["resumen", "jornadas", "competiciones", "versiones"];

// "Rendimiento del modelo" (pedido real del usuario -- sustituye tanto
// al antiguo "Estadísticas" como al "Histórico de jornadas" por
// separado): centro unico de evaluacion historica del motor predictivo,
// con 4 subsecciones (ver ModelPerformanceTabs.tsx). Nunca es un simple
// historial de marcadores: todo lo que se ve aqui viene de predicciones
// que se guardaron ANTES del pitido inicial (`services/prediction_
// service.py`) y de resultados reales liquidados DESPUES
// (`services/evaluation_service.py`) -- nada se recalcula con criterios
// de hoy.
export default async function ModelPerformancePage({
  searchParams,
}: {
  searchParams: { tab?: string };
}) {
  const initialTab = VALID_TABS.includes(searchParams.tab as Tab) ? (searchParams.tab as Tab) : "resumen";
  const [report, history] = await Promise.all([
    getModelPerformance().catch(() => ({ segments: [] })),
    // Limite alto, sin filtrar por liga server-side: la subseccion
    // "Jornadas" agrupa/filtra en el cliente (mismo patron que el
    // antiguo /history), para poder cambiar de liga sin otra ida y
    // vuelta al backend para la vista por defecto.
    getHistory(undefined, 150).catch(() => []),
  ]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
          <BarChart3 className="h-5 w-5 text-accent" />
          Rendimiento del modelo
        </h1>
        <p className="text-sm text-slate-500">
          Cada predicción se registra ANTES del pitido inicial (fotografía inmutable: probabilidad,
          cuota, edge y versión del modelo en ese momento, nunca recalculada después) y se liquida
          contra el resultado real en cuanto el partido termina. Nada aquí es una garantía de
          resultados futuros: es lo que ha pasado hasta ahora, con los datos disponibles.
        </p>
      </div>

      <ModelPerformanceTabs segments={report.segments} historyRows={history} initialTab={initialTab} />
    </div>
  );
}
