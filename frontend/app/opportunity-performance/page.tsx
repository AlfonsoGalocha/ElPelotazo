import Link from "next/link";
import { getBotBankroll, getPublications, getTopSignals } from "@/lib/api";
import BotBankrollBoard from "@/components/BotBankrollBoard";
import OpportunityEquity from "@/components/OpportunityEquity";
import { PublicationRows } from "@/components/PublicationBoard";

export default async function OpportunityPerformance() {
  const [report, bot, current] = await Promise.all([
    getPublications().catch(() => null),
    getBotBankroll().catch(() => null),
    getTopSignals({ limit: 10, scope: "current_round" }, true).catch(() => null),
  ]);
  const p = report?.performance;
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Rendimiento de oportunidades</h1>
      <BotBankrollBoard report={bot} />
      <details className="space-y-3 rounded-xl border border-surface-border p-4">
      <summary className="cursor-pointer font-medium">Histórico de recomendaciones oficiales</summary>
      <p className="text-sm text-slate-400">Resultados teóricos con una unidad por recomendación registrada en el corte oficial.</p>
      {p && p.n > 0 && <dl className="grid grid-cols-2 gap-3 rounded-xl border border-surface-border p-4 sm:grid-cols-4">
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
      {report && p?.n === 0 && (
        <section className="rounded-xl border border-surface-border bg-card p-5">
          <h2 className="font-semibold">Todavía no hay recomendaciones oficiales registradas</h2>
          <p className="mt-2 text-sm text-slate-400">El resultado y el ROI aparecerán cuando termine un partido con una recomendación registrada antes del inicio.</p>
          {current && <p className="mt-3 text-sm text-slate-300">
            La jornada tiene {current.length} selecciones en el Top 10. {current.filter(row => row.has_statistical_value).length} cumplen ahora el filtro de valor.
          </p>}
          <Link href="/top-signals" className="mt-3 inline-block text-sm text-accent underline">Ver selecciones de la jornada</Link>
        </section>
      )}
      {report && p && p.n > 0 && <PublicationRows rows={report.recommendations} />}
      {!report && <p>No se pudo consultar el rendimiento.</p>}
      </details>
    </main>
  );
}
