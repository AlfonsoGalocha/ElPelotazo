import { getPublications } from "@/lib/api";
import OpportunityEquity from "@/components/OpportunityEquity";
import { PublicationRows } from "@/components/PublicationBoard";

export default async function OpportunityPerformance() {
  const report = await getPublications().catch(() => null);
  const p = report?.performance;
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Rendimiento de oportunidades</h1>
      <p className="text-sm text-slate-400">Resultados teóricos con una unidad por recomendación registrada en el corte oficial.</p>
      {p && <dl className="grid grid-cols-2 gap-3 rounded-xl border border-surface-border p-4 sm:grid-cols-4">
        {[
          ["Registradas", p.n], ["Liquidadas",p.settled], ["Aciertos", p.wins], ["Fallos",p.losses], ["Anuladas",p.void],
          ["ROI",p.roi == null ? "—" : `${(p.roi*100).toFixed(1)} %`],
          ["Beneficio teórico",`${p.profit_units.toFixed(2)} u`], ["Caída máxima",`${p.drawdown_units.toFixed(2)} u`],
        ].map(([label,value]) => <div key={label}><dt className="text-sm text-slate-400">{label}</dt><dd>{value}</dd></div>)}
      </dl>}
      {report && <OpportunityEquity rows={report.recommendations} />}
      {report && Object.entries({
        competition: "Por liga", market: "Por mercado", matchday: "Por jornada",
        odds: "Por cuota", probability: "Por probabilidad", version: "Por versión del ranking",
      }).map(([key, title]) => report.breakdown[key]?.length ? (
        <details key={key} className="rounded-xl border border-surface-border p-3">
          <summary className="cursor-pointer font-medium">{title}</summary>
          {report.breakdown[key].map(row => (
            <p className="text-sm text-slate-400" key={row.label}>
              {row.label} · n={row.n} · {row.wins} aciertos · ROI {(row.roi*100).toFixed(1)} %
              {row.n < 30 ? " · muestra pequeña" : ""}
            </p>
          ))}
        </details>
      ) : null)}
      {report ? <PublicationRows rows={report.recommendations} /> : <p>No se pudo consultar el rendimiento.</p>}
    </main>
  );
}
