import Link from "next/link";
import { formatMatchDateTime } from "@/lib/dates";
import { getPublication } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";

const REASON_LABELS: Record<string, string> = {
  cuota_desactualizada: "Cuota antigua",
  cuota_fuera_de_banda: "Fuera del rango de la estrategia",
  calibracion_no_disponible: "Probabilidad original sin calibrar",
  calibracion_en_extremo: "Probabilidad calibrada en 0 % o 100 %",
  ajuste_calibracion_sin_respaldo: "Ajuste de calibración sin respaldo suficiente",
  ev_estimado_no_positivo: "Sin valor esperado a esa cuota",
  sin_mercado: "Sin cuota para ese mercado",
  origen_cuota_no_verificado: "Hora de la cuota sin verificar",
  ventaja_extrema_frente_al_mercado: "Diferencia demasiado grande frente al mercado",
  mercado_contradictorio: "Pronósticos contradictorios",
};

export default async function CapturedPrediction({ params }: { params: { id: string } }) {
  const publication = await getPublication(Number(params.id)).catch(() => null);
  if (!publication) return <p>No se pudo consultar la captura oficial de este partido.</p>;

  const quoted = publication.ranking.filter(row => row.odds != null && Array.isArray(row.reasons))
    .sort((a, b) => (b.calibrated ?? b.probability) - (a.calibrated ?? a.probability));
  const uncalibrated = quoted.filter(row => row.reasons?.includes("calibracion_no_disponible")).length;
  const recommendation = publication.recommendation;

  return (
    <main className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">Captura prepartido</h1>
        <p className="mt-1 text-sm text-slate-400">{formatMatchDateTime(publication.captured_at)}</p>
      </div>
      <section className="rounded-xl border border-surface-border bg-card p-4">
        <h2 className="font-semibold">{recommendation ? "Recomendación oficial" : "Sin recomendación oficial"}</h2>
        {recommendation ? (
          <p className="mt-2 text-sm text-slate-300">
            {MARKET_DISPLAY_NAMES[recommendation.market] ?? recommendation.market}
            {recommendation.odds != null && ` · Cuota ${recommendation.odds.toFixed(2)}`}
          </p>
        ) : (
          <p className="mt-2 text-sm text-slate-300">
            {quoted.length
              ? `${quoted.length} selecciones tenían cuota apta para evaluar en el corte. ${uncalibrated ? `${uncalibrated} tenían la probabilidad sin calibrar.` : "Comprueba los descartes en la tabla."}`
              : "La captura no registra una selección con cuota apta para evaluar."}
          </p>
        )}
      </section>

      {quoted.length > 0 && (
        <section className="space-y-2">
          <h2 className="font-semibold">Selecciones con cuota en el corte</h2>
          <div className="overflow-x-auto rounded-lg border border-surface-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-card text-slate-400">
                <tr>{["Selección", "Probabilidad", "Cuota", "Decisión"].map(label =>
                  <th key={label} scope="col" className="px-3 py-2">{label}</th>
                )}</tr>
              </thead>
              <tbody>
                {quoted.map(row => (
                  <tr key={row.market} className="border-t border-surface-border">
                    <td className="px-3 py-2">{MARKET_DISPLAY_NAMES[row.market] ?? row.market}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{((row.calibrated ?? row.probability) * 100).toFixed(1)} %</td>
                    <td className="px-3 py-2">{row.odds?.toFixed(2)}</td>
                    <td className="px-3 py-2 text-xs text-slate-400">
                      {row.reasons?.length
                        ? row.reasons.map(reason => REASON_LABELS[reason] ?? reason.replaceAll("_", " ")).join(" · ")
                        : recommendation?.market === row.market ? "Recomendada" : "Elegible"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <details className="rounded-lg border border-surface-border p-3 text-sm text-slate-300">
        <summary className="cursor-pointer">Todos los mercados y motivos del corte</summary>
        <ul className="mt-2 space-y-1">
          {publication.ranking.map(row => (
            <li key={row.market}>
              {MARKET_DISPLAY_NAMES[row.market] ?? row.market} · {(row.probability * 100).toFixed(1)} % original
              {row.odds != null && ` · Cuota ${row.odds.toFixed(2)}`}
              {row.reasons?.length ? ` · ${row.reasons.map(reason => REASON_LABELS[reason] ?? reason.replaceAll("_", " ")).join(" · ")}` : ""}
            </li>
          ))}
        </ul>
      </details>

      {publication.feature_snapshots.length > 0 && (
        <details className="rounded-lg border border-surface-border p-3 text-sm text-slate-400">
          <summary className="cursor-pointer">Datos técnicos de la captura</summary>
          <p className="mt-2">Versión {publication.ranking_version}</p>
          {publication.feature_snapshots.map(snapshot => (
            <details key={snapshot.id} className="mt-2">
              <summary className="cursor-pointer">Variables originales del equipo {snapshot.is_home ? "local" : "visitante"}</summary>
              <pre className="mt-2 overflow-x-auto text-xs">{JSON.stringify(snapshot.features, null, 2)}</pre>
            </details>
          ))}
        </details>
      )}
      <Link href={`/history/${params.id}`} className="text-sm text-accent underline">Consultar resultado y liquidación</Link>
    </main>
  );
}
