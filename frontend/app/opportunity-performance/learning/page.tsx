import Link from "next/link";
import { getModelLearning } from "@/lib/api";

const timestamp = new Intl.DateTimeFormat("es-ES", {
  timeZone: "Europe/Madrid", dateStyle: "short", timeStyle: "short",
});
const number = (value: number) => value.toLocaleString("es-ES", { maximumFractionDigits: 4 });

export default async function LearningPage() {
  const rows = await getModelLearning().catch(() => null);
  return (
    <main className="space-y-4">
      <Link href="/model" className="text-sm text-accent">Volver a Rendimiento del modelo</Link>
      <h1 className="text-2xl font-bold">Aprendizaje del modelo</h1>
      <p className="text-sm text-slate-400">Comparación de pronósticos congelados antes del inicio. La promoción sigue bloqueada mientras falte certificar la disponibilidad histórica de los datos.</p>
      {!rows && <p>No se pudo consultar el aprendizaje.</p>}
      {rows?.map(row => (
        <section key={row.competition} className="space-y-2 rounded-lg border border-surface-border bg-card p-4">
          <h2 className="font-semibold">{row.name}</h2>
          <p className="text-sm text-slate-300">Titular {row.titular ? `#${row.titular.id}` : "sin versión en servicio"} · Candidato {row.candidate ? `#${row.candidate.id}` : "pendiente"}</p>
          {row.titular?.certification === "inconsistent_training_cutoff" && (
            <p className="text-sm text-amber-300">Titular heredado con fecha de entrenamiento incoherente. Procedencia temporal sin certificar.</p>
          )}
          {row.candidate && (
            <p className="text-sm text-slate-400">Entrenado {timestamp.format(new Date(row.candidate.trained_at))} · Última jornada en ajuste {row.candidate.last_round ?? "sin certificar"} · Partidos nuevos en ajuste {row.candidate.new_fit_matches ?? "sin verificar"}</p>
          )}
          <p className="text-sm text-amber-300">
            {row.comparison_status === "no_titular" ? "Sin titular para comparar" : row.comparison_status === "insufficient_sample" ? "Muestra prospectiva insuficiente" : "Comparación prospectiva disponible"} · Promoción bloqueada
          </p>
          {Object.keys(row.metrics).length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full whitespace-nowrap text-left text-sm">
                <thead><tr>{["Mercado", "Partidos", "Brier titular", "Brier candidato", "Log Loss titular", "Log Loss candidato"].map(label => <th key={label} className="px-2 py-1">{label}</th>)}</tr></thead>
                <tbody>{Object.entries(row.metrics).map(([market, metric]) => (
                  <tr className="border-t border-surface-border" key={market}>
                    {[market, metric.matches, number(metric.titular.brier), number(metric.candidate.brier), number(metric.titular.log_loss), number(metric.candidate.log_loss)].map((value, index) => <td key={index} className="px-2 py-1">{value}</td>)}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <details className="text-sm text-slate-400">
            <summary className="cursor-pointer">Historial de versiones</summary>
            {row.history.map(version => <p key={version.id}>#{version.id} · {timestamp.format(new Date(version.created_at))} · {{in_service:"En servicio", inactive:"Sin promover", legacy:"Heredada"}[version.state]}</p>)}
          </details>
        </section>
      ))}
    </main>
  );
}
