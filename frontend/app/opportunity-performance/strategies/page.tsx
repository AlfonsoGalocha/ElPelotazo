import Link from "next/link";
import { getCompetitions, getShadowOperations, getShadowReport } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";
import { MARKET_DISPLAY_NAMES } from "@/types";
import Operations from "./Operations";

const pct = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });
const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const labels: Record<string, string> = {
  original: "Modelo original", corregida: "Calibración corregida", mercado: "Mercado independiente",
  mixta_80_20: "Mezcla 80/20", mixta_65_35: "Mezcla 65/35", mixta_50_50: "Mezcla 50/50",
  mixta_35_65: "Mezcla 35/65", mixta_20_80: "Mezcla 20/80",
};
const outcomes: Record<string, string> = {
  won: "Acierto", lost: "Fallo", void: "Anulada", pending: "Pendiente", unverified: "Sin verificar",
};
const passes: Record<string, string> = { no_prediction: "Sin pronóstico", no_valid_quotes: "Sin cuota verificable",
  no_independent_reference: "Sin referencia independiente", no_eligible_market: "Ningún mercado cumple los filtros" };
const rate = (n: number | null) => n === null ? "—" : pct.format(n);
const num = (n: number | null) => n === null ? "—" : n.toFixed(3);
const money = (n: number) => euros.format(n / 100);

export default async function StrategiesPage({ searchParams }: { searchParams: { band?: string; competition?: string } }) {
  const [report, operations, competitions] = await Promise.all([
    getShadowReport(searchParams.band, searchParams.competition).catch(() => null),
    getShadowOperations().catch(() => null),
    getCompetitions().catch(() => []),
  ]);
  return (
    <main className="space-y-5">
      <Link href="/opportunity-performance" className="text-sm text-accent">Volver a Rendimiento</Link>
      <h1 className="text-2xl font-bold">Laboratorio de estrategias</h1>
      <div className="text-sm text-slate-400">
        <p>Simulación prospectiva · En evaluación · 10 € virtuales por selección.</p>
        <p>La estrategia activa de la app funciona por separado.</p>
        <p><Link className="text-accent underline" href="/opportunity-performance#acierto">Ver la jornada anterior y la simulación retrospectiva de 10 €</Link></p>
      </div>
      {operations ? <Operations report={operations} /> : <p className="text-sm text-slate-400">Estado operativo temporalmente no disponible.</p>}
      {!report ? <p>No se pudo consultar la comparación.</p> : (
        <>
          <form action="/opportunity-performance/strategies" className="flex items-center gap-2 text-sm">
            <input type="hidden" name="band" value={report.band} />
            <label htmlFor="competition">Competición</label>
            <select id="competition" name="competition" defaultValue={searchParams.competition ?? ""} className="rounded border border-surface-border bg-card p-1">
              <option value="">Todas</option>
              <option value="continental_champions">Champions UEFA y AFC Elite</option>
              {competitions.filter(c => ["laliga", "premier_league", "bundesliga", "serie_a", "ligue_1", "champions_league", "liga_f", "afc_champions_league"].includes(c.code))
                .map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
            </select>
            <button type="submit" className="rounded border border-surface-border px-2 py-1">Aplicar</button>
          </form>
          <nav aria-label="Rango de cuotas experimental" className="flex flex-wrap gap-2">
            {Object.keys(report.bands).map(band => (
              <Link key={band} href={`?band=${encodeURIComponent(band)}${searchParams.competition ? `&competition=${encodeURIComponent(searchParams.competition)}` : ""}`}
                aria-current={report.band === band ? "page" : undefined}
                className={`rounded border px-3 py-1 text-sm ${report.band === band ? "border-accent text-accent" : "border-surface-border text-slate-300"}`}>
                {band === "principal" ? "Principal · 1,35–1,90" : band.replaceAll(".", ",").replace("-", "–")}
              </Link>
            ))}
          </nav>
          <p className="text-sm text-slate-400">
            {report.fixtures ? `${report.fixtures} partidos con corte prepartido registrado.` : "El primer corte aparecerá en la hora previa al inicio de un partido cubierto."}
          </p>
          <section className="overflow-x-auto rounded-lg border border-surface-border bg-card p-4">
            <h2 className="font-semibold">Resultados a cuota ejecutable</h2>
            <table className="mt-2 w-full whitespace-nowrap text-left text-sm">
              <thead><tr>{["Estrategia", "Selecciones", "Liquidadas", "A/F", "Acierto", "Neto", "ROI", "Caída máx.", "Cuota media", "Prob. media", "EV medio"].map(x => <th className="pr-3 py-2" key={x}>{x}</th>)}</tr></thead>
              <tbody>{Object.entries(report.strategies).map(([name, row]) => (
                <tr key={name} className="border-t border-surface-border">
                  {[labels[name], row.selections, row.settled, `${row.wins}/${row.losses}`, rate(row.accuracy), row.settled ? money(row.profit_cents) : "—",
                    rate(row.roi), row.settled ? money(row.drawdown_cents) : "—", num(row.mean_odds), rate(row.mean_probability), rate(row.mean_ev)]
                    .map((value, i) => <td className="pr-3 py-2" key={i}>{value}</td>)}
                </tr>
              ))}</tbody>
            </table>
          </section>
          <section className="rounded-lg border border-surface-border bg-card p-4">
            <h2 className="font-semibold">Calidad probabilística en mercados comunes</h2>
            <p className="text-sm text-slate-400">Mismas observaciones para las ocho estrategias. Este universo es independiente del rango experimental seleccionado.</p>
            <table className="mt-2 w-full text-left text-sm">
              <thead><tr><th>Estrategia</th><th>Mercados</th><th>Brier</th><th>Log Loss</th></tr></thead>
              <tbody>{Object.entries(report.strategies).map(([name, { common_quality: q }]) => (
                <tr key={name} className="border-t border-surface-border">
                  <td className="py-2">{labels[name]}</td><td>{q.n}</td><td>{num(q.brier)}{q.brier_interval && ` [${q.brier_interval.map(num).join("–")}]`}</td><td>{num(q.log_loss)}</td>
                </tr>
              ))}</tbody>
            </table>
          </section>
          {Object.entries(report.strategies).map(([name, row]) => row.selections > 0 && (
            <details key={name} className="rounded-lg border border-surface-border p-3">
              <summary className="cursor-pointer">{labels[name]} · Evolución y desgloses</summary>
              {row.roi_interval && <p className="mt-2 text-sm">Intervalo descriptivo de ROI por bloques de fecha · {row.roi_interval.map(rate).join(" a ")}</p>}
              <div className="mt-3 grid gap-4 md:grid-cols-2">
                <div>
                  <h3 className="text-sm font-semibold">Beneficio acumulado</h3>
                  <ol className="max-h-56 overflow-auto text-sm text-slate-300">
                    {row.curve.map((point) => <li key={point.at}>{formatMatchDateTime(point.at)} · {money(point.profit_cents)}</li>)}
                  </ol>
                </div>
                {([["Por liga", row.by_league], ["Por mercado", row.by_market], ["Por cuota", row.by_odds]] as const).map(([title, groups]) => (
                  <div key={title}>
                    <h3 className="text-sm font-semibold">{title}</h3>
                    <ul className="text-sm text-slate-300">
                      {Object.entries(groups).map(([key, stats]) => (
                        <li key={key}>{MARKET_DISPLAY_NAMES[key] ?? key} · {stats.settled} liquidadas · {money(stats.profit_cents)} · ROI {rate(stats.roi)}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </details>
          ))}
          <section className="space-y-2">
            <h2 className="font-semibold">Historial de decisiones prepartido</h2>
            {report.history.map((fixture) => (
              <details key={fixture.match_id} className="rounded-lg border border-surface-border p-3">
                <summary className="cursor-pointer">{fixture.match} · {formatMatchDateTime(fixture.capture)}</summary>
                <ul className="mt-2 space-y-1 text-sm text-slate-300">
                  {Object.entries(fixture.decisions).map(([key, pick]) => (
                    <li key={key}>{labels[key]} · {pick ? `${MARKET_DISPLAY_NAMES[pick.market] ?? pick.market} · ${rate(pick.probability)} · ${pick.price.toFixed(2)} · ${pick.bookmaker} · ${outcomes[pick.outcome]} ${key === "corregida" && pick.corrected_source.startsWith("original") ? "· Respaldo original" : ""}` : "Sin selección"}</li>
                  ))}
                </ul>
              </details>
            ))}
          </section>
        </>
      )}
    </main>
  );
}
