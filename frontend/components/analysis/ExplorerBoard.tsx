"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import TeamLogo from "@/components/TeamLogo";
import { getCompetitions } from "@/lib/api";
import { useCachedData } from "@/lib/clientCache";
import { fmt, getExplorer, getTeamDirectory, lineLabel, pct, type ExplorerResult, type TeamDirectoryEntry } from "@/lib/analysis";
import { MARKET_DISPLAY_NAMES, type Competition } from "@/types";
import { EmptyNote, Segmented } from "./StatsKit";

const STAT_METRICS: [string, string][] = [
  ["goals_avg", "Media de goles"], ["over_1_5", "Over 1,5 goles (frecuencia)"], ["over_2_5", "Over 2,5 goles (frecuencia)"],
  ["btts", "Ambos marcan (frecuencia)"], ["corners_avg", "Media de córners"], ["shots_avg", "Media de tiros"],
  ["shots_on_target_avg", "Media de tiros a puerta"], ["line", "Frecuencia de una línea concreta…"],
];
const MODEL_MARKETS = ["over_1_5", "over_2_5", "under_2_5", "over_3_5", "btts", "home_win", "draw", "away_win",
  "double_chance_1x", "double_chance_x2", "corners_over_8_5", "corners_over_9_5", "corners_over_10_5", "corners_under_9_5"];
const FAMILIES: [string, string, string][] = [["goals", "Goles totales", "2.5"], ["corners", "Córners totales", "9.5"],
  ["shots", "Tiros totales", "24.5"], ["shots_on_target", "Tiros a puerta totales", "8.5"]];
const time = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const isoDay = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

export default function ExplorerBoard() {
  const [metric, setMetric] = useState("goals_avg");
  const [market, setMarket] = useState("over_2_5");
  const [family, setFamily] = useState("corners");
  const [line, setLine] = useState("9.5");
  const [range, setRange] = useState<[string, string]>([isoDay(0), isoDay(3)]);
  const [competition, setCompetition] = useState("");
  const [teamQuery, setTeamQuery] = useState("");
  const [window, setWindow] = useState(10);
  const [venue, setVenue] = useState<"split" | "all">("split");
  const [scope, setScope] = useState<"all" | "competition">("all");
  const [minSample, setMinSample] = useState(5);
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const competitions = useCachedData<Competition[]>("competitions", getCompetitions).data ?? [];
  const directory = useCachedData<TeamDirectoryEntry[]>("team-directory", getTeamDirectory).data ?? [];
  const team = directory.find(t => t.name.toLowerCase() === teamQuery.trim().toLowerCase());
  const predictive = metric === "model";
  const params = {
    metric, market: predictive ? market : undefined, family: metric === "line" ? family : undefined,
    line: metric === "line" ? line : undefined, date_from: range[0], date_to: range[1], competition: competition || undefined,
    team_id: team?.id, window, venue, scope, min_sample: minSample, order,
  };
  const key = `explorer:${JSON.stringify(params)}`;
  const { data, error, refreshing } = useCachedData<ExplorerResult>(key, () => getExplorer(params), { delayMs: 200 });
  const isRate = !predictive && !metric.endsWith("_avg");
  const show = (v: number | null) => (predictive || isRate ? pct(v) : fmt(v));
  const quick = useMemo(() => [["Hoy", 0], ["3 días", 3], ["7 días", 7]] as const, []);

  return (
    <div className="space-y-3">
      <section className="space-y-2 rounded-xl border border-surface-border bg-card p-3">
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Métrica" value={metric} onChange={e => setMetric(e.target.value)}
                  className="rounded border border-surface-border bg-surface-raised px-2 py-1.5 text-sm">
            <optgroup label="Ranking estadístico (histórico)">{STAT_METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</optgroup>
            <optgroup label="Ranking predictivo (modelo)"><option value="model">Probabilidad del modelo…</option></optgroup>
          </select>
          {predictive && (
            <select aria-label="Mercado" value={market} onChange={e => setMarket(e.target.value)}
                    className="rounded border border-surface-border bg-surface-raised px-2 py-1.5 text-sm">
              {MODEL_MARKETS.map(m => <option key={m} value={m}>{MARKET_DISPLAY_NAMES[m] ?? m}</option>)}
            </select>
          )}
          {metric === "line" && (
            <>
              <select aria-label="Estadística" value={family} onChange={e => { setFamily(e.target.value); setLine(FAMILIES.find(f => f[0] === e.target.value)![2]); }}
                      className="rounded border border-surface-border bg-surface-raised px-2 py-1.5 text-sm">
                {FAMILIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <label className="flex items-center gap-1 text-xs text-slate-400">Más de
                <input type="number" step="1" min="0.5" value={line} onChange={e => setLine(e.target.value)}
                       className="w-16 rounded border border-surface-border bg-surface-raised px-2 py-1 text-sm text-slate-100" />
              </label>
            </>
          )}
          <Segmented label="Orden" value={order} options={[["desc", "Mayor primero"], ["asc", "Menor primero"]]} onChange={setOrder} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented label="Fechas" value={range[1] === isoDay(0) ? 0 : range[1] === isoDay(3) ? 3 : range[1] === isoDay(7) ? 7 : -1}
                     options={quick.map(([l, d]) => [d, l] as [number, string])} onChange={d => setRange([isoDay(0), isoDay(d)])} />
          <input type="date" aria-label="Desde" value={range[0]} onChange={e => setRange([e.target.value, range[1]])}
                 className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-xs" />
          <input type="date" aria-label="Hasta" value={range[1]} onChange={e => setRange([range[0], e.target.value])}
                 className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-xs" />
          <select aria-label="Competición" value={competition} onChange={e => setCompetition(e.target.value)}
                  className="rounded border border-surface-border bg-surface-raised px-2 py-1.5 text-xs">
            <option value="">Todas las competiciones</option>
            {competitions.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
          </select>
          <input list="explorer-teams" aria-label="Equipo" placeholder="Equipo…" value={teamQuery} onChange={e => setTeamQuery(e.target.value)}
                 className="w-40 rounded border border-surface-border bg-surface-raised px-2 py-1 text-xs" />
          <datalist id="explorer-teams">{directory.map(t => <option key={t.id} value={t.name} />)}</datalist>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented label="Ventana" value={window} options={[[5, "Últ. 5"], [10, "Últ. 10"], [20, "Últ. 20"]]} onChange={setWindow} />
          <Segmented label="Condición" value={venue} options={[["split", "Local en casa · visitante fuera"], ["all", "General"]]} onChange={setVenue} />
          <Segmented label="Competiciones de la muestra" value={scope} options={[["all", "Todas"], ["competition", "Solo la del partido"]]} onChange={setScope} />
          <label className="flex items-center gap-1 text-xs text-slate-400">Muestra mínima
            <input type="number" min={0} max={60} value={minSample} onChange={e => setMinSample(Number(e.target.value))}
                   className="w-14 rounded border border-surface-border bg-surface-raised px-2 py-1 text-sm text-slate-100" />
          </label>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded px-2 py-0.5 font-semibold ${predictive ? "bg-violet-500/15 text-violet-300" : "bg-sky-500/15 text-sky-300"}`}>
          {predictive ? "Ranking predictivo" : "Ranking estadístico"}
        </span>
        <span className="text-slate-400">
          {predictive ? "Probabilidad guardada del modelo. No sustituye al ranking de mejores oportunidades."
            : "Frecuencias y medias históricas de ambos equipos (antes del partido). No son probabilidades."}
        </span>
        {refreshing && <span className="text-slate-500">Actualizando…</span>}
      </div>

      {error && <EmptyNote>No se pudo consultar el explorador.</EmptyNote>}
      {!data && !error && <div className="h-64 animate-pulse rounded-lg bg-surface-alt/40" aria-label="Cargando" />}
      {data && !data.rows.length && <EmptyNote>Ningún partido cumple los filtros (prueba a bajar la muestra mínima o ampliar fechas).</EmptyNote>}
      {data && data.rows.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-surface-border bg-card">
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-400"><tr>
              <th scope="col" className="px-3 py-2 text-left">Partido</th>
              <th scope="col" className="px-3 py-2 text-right">
                <button type="button" onClick={() => setOrder(order === "desc" ? "asc" : "desc")} className="hover:text-slate-200">
                  {metric === "line" ? `Más de ${lineLabel(line)}` : data.label} {order === "desc" ? "↓" : "↑"}
                </button>
              </th>
              {!predictive && <th scope="col" className="hidden px-3 py-2 text-right sm:table-cell">Local · Visitante</th>}
              <th scope="col" className="px-3 py-2 text-right" title="Partidos con dato en cada muestra / solicitados">Muestra</th>
            </tr></thead>
            <tbody>
              {data.rows.map(row => (
                <tr key={row.match_id} className="border-t border-surface-border hover:bg-surface-alt/40">
                  <td className="px-3 py-2">
                    <Link href={`/matches/${row.match_id}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <span className="flex items-center gap-1.5 font-medium">
                        <TeamLogo name={row.home.name} logoUrl={row.home.logo_url} size="sm" />{row.home.name}
                        <span className="text-slate-500">–</span>
                        <TeamLogo name={row.away.name} logoUrl={row.away.logo_url} size="sm" />{row.away.name}
                      </span>
                      <span className="w-full text-xs text-slate-500">{time.format(new Date(row.kickoff))} · {row.competition.name}</span>
                    </Link>
                  </td>
                  <td className={`px-3 py-2 text-right font-semibold tabular-nums ${predictive ? "text-violet-300" : "text-slate-100"}`}>{show(row.value)}</td>
                  {!predictive && (
                    <td className="hidden px-3 py-2 text-right text-xs tabular-nums text-slate-400 sm:table-cell">
                      {show(row.home_value)} · {show(row.away_value)}
                      {row.counts && <span className="block">{row.counts.home}/{row.sample.home} · {row.counts.away}/{row.sample.away}</span>}
                    </td>
                  )}
                  <td className={`px-3 py-2 text-right text-xs ${Math.min(row.sample.home, row.sample.away) < row.sample.requested ? "text-amber-300" : "text-slate-400"}`}>
                    {row.sample.home} · {row.sample.away}<span className="text-slate-500"> /{row.sample.requested}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
