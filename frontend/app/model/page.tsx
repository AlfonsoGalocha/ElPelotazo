import { BarChart3 } from "lucide-react";
import { getHistory, getModelPerformance, getPublicationCoverage, getPublications } from "@/lib/api";
import CalibrationChart from "@/components/CalibrationChart";
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
  const [report, history, publications, coverage] = await Promise.all([
    getModelPerformance().catch(() => ({ segments: [] })),
    // Limite alto, sin filtrar por liga server-side: la subseccion
    // "Jornadas" agrupa/filtra en el cliente (mismo patron que el
    // antiguo /history), para poder cambiar de liga sin otra ida y
    // vuelta al backend para la vista por defecto.
    getHistory(undefined, 150).catch(() => []),
    getPublications().catch(() => null),
    getPublicationCoverage().catch(() => null),
  ]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
          <BarChart3 className="h-5 w-5 text-accent" />
          Laboratorio del modelo
        </h1>
        <p className="text-sm text-slate-400">
          Aquí puedes ver cuántos pronósticos acierta el modelo y si los porcentajes que anuncia coinciden con lo que ocurre.
        </p>
      </div>
      {coverage && <section className="rounded-xl border border-surface-border bg-card p-4">
        <h2 className="font-semibold">Qué datos cuentan</h2>
        <p className="mt-2 text-sm text-slate-300">
          Hay {coverage.historical_liquidated.toLocaleString("es-ES")} pronósticos históricos liquidados. {coverage.historical_before_kickoff.toLocaleString("es-ES")} constan como registrados antes del inicio y {coverage.historical_after_kickoff.toLocaleString("es-ES")} se excluyen porque se registraron al inicio o después.
        </p>
        <p className="mt-2 text-sm text-slate-400">
          El seguimiento oficial tiene {coverage.official_captures} partidos capturados y {coverage.official_finished} finalizados. Las recomendaciones registradas se evalúan por separado.
        </p>
      </section>}
      <section className="grid gap-4 lg:grid-cols-3" aria-label="Capturas prepartido">
        {publications?.metrics.length ? publications.metrics.map(metric => (
          <article key={metric.family} className="rounded-xl border border-surface-border p-4">
            <h2 className="font-semibold">{metric.family}</h2>
            <p className="text-sm text-slate-300">{metric.n} partidos · {metric.wins} aciertos · {metric.losses} fallos · {(100 * metric.wins / metric.n).toFixed(1)} %</p>
            <p className="text-xs text-slate-400">Brier {metric.brier.toFixed(3)} · Log Loss {metric.log_loss.toFixed(3)} · ECE {metric.ece.toFixed(3)}</p>
            <CalibrationChart buckets={metric.calibration.map(b => ({
              range: `${Math.round(b.low * 100)}–${Math.round(b.high * 100)} %`,
              n: b.n, predicted_probability_mean: b.predicted, empirical_frequency: b.observed,
            }))} />
            <details className="text-sm text-slate-300">
              <summary className="cursor-pointer">Por competición y modelo</summary>
              {metric.segments.map(s => (
                <p key={`${s.competition}-${s.model_version}`}>
                  {s.competition} · Modelo {s.model_version} · n={s.n} · Brier {s.brier.toFixed(3)} · Log Loss {s.log_loss.toFixed(3)}
                </p>
              ))}
            </details>
          </article>
        )) : <p className="text-sm text-slate-400">
          {publications ? "La evaluación de las capturas nuevas aparecerá al finalizar los primeros partidos registrados. Abajo tienes las estadísticas anteriores." : "No se pudieron consultar las capturas oficiales."}
        </p>}
      </section>
      <h2 className="text-lg font-semibold">Estadísticas históricas</h2>
      <ModelPerformanceTabs segments={report.segments} historyRows={history} initialTab={initialTab} />
    </div>
  );
}
