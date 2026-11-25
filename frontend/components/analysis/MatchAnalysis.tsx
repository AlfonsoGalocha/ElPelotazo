"use client";

import Link from "next/link";
import { useState } from "react";
import { useCachedData } from "@/lib/clientCache";
import { getMatchAnalysis, fmt, pct, type AnalysisFilters, type MatchAnalysis as Analysis, type TeamRow } from "@/lib/analysis";
import { MARKET_DISPLAY_NAMES } from "@/types";
import OddsCentre from "./OddsCentre";
import StrategyTop3 from "./StrategyTop3";
import {
  CompareBar, Distribution, EmptyNote, FormString, LineTable, Panel, RecentMatches, SampleBadge, Segmented,
  SummaryStats, TeamLegend, TrendChart, describeBar, rateBar,
} from "./StatsKit";

const TABS = [
  ["resumen", "Resumen"], ["estadisticas", "Estadísticas"], ["goles", "Goles"], ["corners", "Córners"],
  ["tiros", "Tiros"], ["predicciones", "Predicciones"], ["cuotas", "Cuotas"], ["h2h", "Enfrentamientos"],
] as const;
type Tab = typeof TABS[number][0];

const lineKey = (prefix: string, line: string) => `${prefix}${line.replace(".", "_")}`;

function modelCell(data: Analysis, market: string) {
  const entry = data.markets[market];
  if (!entry) return undefined;
  const model = entry.model;
  return { probability: model ? (model.calibrated ?? model.original) : null, implied: entry.market?.implied ?? null };
}

function Filters({ value, onChange, competition }: { value: AnalysisFilters; onChange: (f: AnalysisFilters) => void; competition: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented label="Ventana" value={value.season === "current" ? 0 : value.window}
                 options={[[5, "Últ. 5"], [10, "Últ. 10"], [20, "Últ. 20"], [0, "Temporada"]]}
                 onChange={v => onChange(v === 0 ? { ...value, season: "current", window: 60 } : { ...value, season: "all", window: v })} />
      <Segmented label="Competición" value={value.scope}
                 options={[["all", "Todas"], ["competition", competition]]} onChange={scope => onChange({ ...value, scope })} />
      <Segmented label="Condición" value={value.venue}
                 options={[["split", "Local en casa · visitante fuera"], ["all", "General"]]} onChange={venue => onChange({ ...value, venue })} />
    </div>
  );
}

function Coverage({ data }: { data: Analysis }) {
  const items = [["goals", "Goles"], ["corners", "Córners"], ["shots", "Tiros"], ["shots_on_target", "A puerta"], ["possession", "Posesión"]] as const;
  return (
    <p className="text-xs text-slate-400">
      Cobertura de la muestra · {items.map(([key, name]) => (
        <span key={key} className="mr-2 whitespace-nowrap">{name} {data.home.coverage[key]?.n ?? 0}/{data.home.coverage[key]?.of ?? 0} · {data.away.coverage[key]?.n ?? 0}/{data.away.coverage[key]?.of ?? 0}</span>
      ))}
    </p>
  );
}

function Summary({ data }: { data: Analysis }) {
  const h = data.home.analysis.profile, a = data.away.analysis.profile;
  const highlights = Object.entries(data.markets)
    .filter(([, v]) => v.model && (v.model.calibrated ?? v.model.original) != null)
    .sort(([, x], [, y]) => (y.model!.calibrated ?? y.model!.original ?? 0) - (x.model!.calibrated ?? x.model!.original ?? 0))
    .slice(0, 4);
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Panel title="Comparativa rápida" hint="Medias por partido de la muestra elegida.">
        <TeamLegend home={data.home.team.name} away={data.away.team.name} />
        {describeBar("Goles marcados", h.goals_for, a.goals_for)}
        {describeBar("Goles recibidos", h.goals_against, a.goals_against, false)}
        {describeBar("Córners a favor", h.corners_for, a.corners_for)}
        {describeBar("Tiros", h.shots_for, a.shots_for)}
        {describeBar("Tiros a puerta", h.shots_on_target_for, a.shots_on_target_for)}
        {rateBar("Ambos marcan", h.btts, a.btts)}
        {rateBar("Más de 2,5 goles", { count: h.goals_lines["2.5"].over, n: h.goals_lines["2.5"].n, rate: h.goals_lines["2.5"].rate },
                 { count: a.goals_lines["2.5"].over, n: a.goals_lines["2.5"].n, rate: a.goals_lines["2.5"].rate })}
      </Panel>
      <div className="space-y-3">
        <Panel title="Forma y clasificación">
          {([data.home, data.away] as const).map(side => (
            <div key={side.team.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-sm">
              <Link href={`/teams/${side.team.id}`} className="font-medium hover:underline">{side.team.name}</Link>
              <span className="text-xs text-slate-400">
                {side.standing ? `${side.standing.position}.º · ${side.standing.points} pts · ${side.standing.won}-${side.standing.drawn}-${side.standing.lost}` : "Sin clasificación"}
              </span>
              <FormString results={side.form} />
            </div>
          ))}
        </Panel>
        <Panel title="Lo que estima el modelo" hint="Lo más probable según el modelo, sin filtros de cuota ni de valor. Son probabilidades predictivas, distintas de las frecuencias históricas.">
          {highlights.length ? (
            <ul className="space-y-1 text-sm">
              {highlights.map(([market, v]) => (
                <li key={market} className="flex justify-between gap-2">
                  <span>{MARKET_DISPLAY_NAMES[market] ?? market}</span>
                  <span className="tabular-nums text-violet-300">{pct(v.model!.calibrated ?? v.model!.original)}
                    {v.market && <span className="ml-2 text-xs text-slate-400">cuota {fmt(v.market.price)} · implícita {pct(v.market.implied)}</span>}
                  </span>
                </li>
              ))}
            </ul>
          ) : <EmptyNote>Sin predicción guardada para este partido.</EmptyNote>}
        </Panel>
        <StrategyTop3 matchId={data.match.id} />
        {data.final && (
          <Panel title="Estadísticas finales" hint="Información posterior al partido: no forma parte de ninguna muestra prepartido.">
            <div className="grid grid-cols-3 gap-1 text-center text-sm">
              {([["Descanso", "goals_ht"], ["Córners", "corners"], ["Tiros", "shots"], ["A puerta", "shots_on_target"], ["Posesión", "possession"]] as const)
                .map(([name, key]) => (
                  <div key={key} className="contents">
                    <span className="tabular-nums">{data.final![`home_${key}`] ?? "—"}</span>
                    <span className="text-xs text-slate-400">{name}</span>
                    <span className="tabular-nums">{data.final![`away_${key}`] ?? "—"}</span>
                  </div>
                ))}
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

function Stats({ data }: { data: Analysis }) {
  const h = data.home.analysis.profile, a = data.away.analysis.profile;
  const wins = (p: typeof h) => ({ count: p.record.W, n: p.matches, rate: p.matches ? p.record.W / p.matches : null });
  const draws = (p: typeof h) => ({ count: p.record.D, n: p.matches, rate: p.matches ? p.record.D / p.matches : null });
  const losses = (p: typeof h) => ({ count: p.record.L, n: p.matches, rate: p.matches ? p.record.L / p.matches : null });
  return (
    <div className="space-y-3">
      <Panel title="Comparador estadístico" hint="Medias por partido; n indica cuántos partidos de la muestra tienen el dato.">
        <TeamLegend home={data.home.team.name} away={data.away.team.name} />
        {describeBar("Goles marcados", h.goals_for, a.goals_for)}
        {describeBar("Goles recibidos", h.goals_against, a.goals_against, false)}
        {describeBar("Goles totales", h.goals_total, a.goals_total)}
        {describeBar("Tiros", h.shots_for, a.shots_for)}
        {describeBar("Tiros a puerta", h.shots_on_target_for, a.shots_on_target_for)}
        {describeBar("Tiros recibidos", h.shots_against, a.shots_against, false)}
        {describeBar("A puerta recibidos", h.shots_on_target_against, a.shots_on_target_against, false)}
        {describeBar("Córners a favor", h.corners_for, a.corners_for)}
        {describeBar("Córners en contra", h.corners_against, a.corners_against, false)}
        {describeBar("Posesión %", h.possession, a.possession)}
        {rateBar("Porterías a cero", h.clean_sheets, a.clean_sheets)}
        {rateBar("Partidos marcando", h.scored, a.scored)}
        {rateBar("Partidos recibiendo", h.conceded, a.conceded, false)}
        {rateBar("Victorias", wins(h), wins(a))}
        {rateBar("Empates", draws(h), draws(a))}
        {rateBar("Derrotas", losses(h), losses(a), false)}
      </Panel>
      <div className="grid gap-3 lg:grid-cols-2">
        <RecentMatches rows={data.home.analysis.matches} title={`${data.home.team.name} · últimos partidos`} />
        <RecentMatches rows={data.away.analysis.matches} title={`${data.away.team.name} · últimos partidos`} />
      </div>
      <Panel title="Tendencias" hint="Evolución partido a partido de la muestra. Es descriptiva: no es una predicción validada.">
        <TrendPicker data={data} metrics={[["Goles marcados", r => r.goals_for], ["Goles recibidos", r => r.goals_against],
          ["Córners a favor", r => r.corners_for], ["Tiros", r => r.shots_for], ["Tiros a puerta", r => r.shots_on_target_for]]} />
      </Panel>
    </div>
  );
}

function TrendPicker({ data, metrics }: { data: Analysis; metrics: [string, (r: TeamRow) => number | null][] }) {
  const [index, setIndex] = useState(0);
  const [name, metric] = metrics[index];
  return (
    <>
      <div className="mb-2"><Segmented label="Métrica" value={index} options={metrics.map(([n], i) => [i, n] as [number, string])} onChange={setIndex} /></div>
      <TrendChart home={data.home.analysis.matches} away={data.away.analysis.matches} homeName={data.home.team.name}
                  awayName={data.away.team.name} metric={metric} label={name} />
    </>
  );
}

function Goals({ data }: { data: Analysis }) {
  const h = data.home.analysis.profile, a = data.away.analysis.profile;
  const lines = Object.keys(h.goals_lines);
  const hasHalves = h.goals_ht_for.n > 0 || a.goals_ht_for.n > 0;
  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Goles por partido">
          <SummaryStats label="Marcados" home={h.goals_for} away={a.goals_for} />
          <SummaryStats label="Recibidos" home={h.goals_against} away={a.goals_against} />
          <SummaryStats label="Totales" home={h.goals_total} away={a.goals_total} />
        </Panel>
        <Panel title="Frecuencias">
          <TeamLegend home={data.home.team.name} away={data.away.team.name} />
          {rateBar("Marca al menos uno", h.scored, a.scored)}
          {rateBar("Recibe al menos uno", h.conceded, a.conceded, false)}
          {rateBar("Portería a cero", h.clean_sheets, a.clean_sheets)}
          {rateBar("Ambos marcan", h.btts, a.btts)}
        </Panel>
      </div>
      <Panel title="Líneas de goles del partido" hint="A y B son frecuencias históricas de cada equipo (sus partidos, no este). C es la probabilidad del modelo y D la implícita en la cuota de consenso.">
        <LineTable title="Goles totales" lines={lines} home={h.goals_lines} away={a.goals_lines} model={line => modelCell(data, lineKey("over_", line))} />
      </Panel>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Goles por equipo" hint="Partidos en los que cada equipo marcó más de la línea.">
          <LineTable title="Goles propios" lines={Object.keys(h.team_goals_lines)} home={h.team_goals_lines} away={a.team_goals_lines} />
        </Panel>
        <Panel title="Distribución de goles totales">
          <Distribution home={h.goals_total} away={a.goals_total} homeName={data.home.team.name} awayName={data.away.team.name} unit="goles" />
        </Panel>
      </div>
      {hasHalves ? (
        <Panel title="Primera y segunda parte" hint="Solo partidos con marcador al descanso registrado.">
          <TeamLegend home={data.home.team.name} away={data.away.team.name} />
          {describeBar("Goles propios 1.ª parte", h.goals_ht_for, a.goals_ht_for)}
          {describeBar("Goles propios 2.ª parte", h.goals_2h_for, a.goals_2h_for)}
          {describeBar("Goles totales al descanso", h.goals_ht_total, a.goals_ht_total)}
          <p className="mt-1 text-xs text-slate-500">No hay minutos de gol almacenados: la distribución temporal se limita a primera y segunda parte.</p>
        </Panel>
      ) : <EmptyNote>Sin marcadores al descanso en esta muestra.</EmptyNote>}
    </div>
  );
}

function Corners({ data }: { data: Analysis }) {
  const h = data.home.analysis.profile, a = data.away.analysis.profile;
  const cornerModels = Object.entries(data.markets).filter(([k, v]) => k.startsWith("corners_") && v.model);
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
        El modelo de córners (Poisson sobre el total) ha mostrado sobreconfianza y calibración insuficiente en el seguimiento oficial.
        Compara siempre su probabilidad con las frecuencias reales y consulta su calibración en{" "}
        <Link href="/model" className="underline">Rendimiento del modelo</Link>.
        {cornerModels.some(([, v]) => v.model!.calibrated != null && v.model!.original != null && Math.abs(v.model!.calibrated - v.model!.original) > .1) &&
          " En este partido la calibración corrige más de 10 puntos alguna probabilidad original."}
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Córners por partido">
          <SummaryStats label="A favor" home={h.corners_for} away={a.corners_for} />
          <SummaryStats label="En contra" home={h.corners_against} away={a.corners_against} />
          <SummaryStats label="Totales" home={h.corners_total} away={a.corners_total} />
        </Panel>
        <Panel title="Distribución de córners totales">
          <Distribution home={h.corners_total} away={a.corners_total} homeName={data.home.team.name} awayName={data.away.team.name} unit="córners" />
        </Panel>
      </div>
      <Panel title="Líneas de córners del partido" hint="Ej.: «7/10» = en 7 de sus últimos 10 partidos con dato se superó la línea.">
        <LineTable title="Córners totales" lines={Object.keys(h.corners_lines)} home={h.corners_lines} away={a.corners_lines}
                   model={line => modelCell(data, lineKey("corners_over_", line))} />
      </Panel>
      {cornerModels.length > 0 && (
        <Panel title="Modelo de córners en este partido" hint="Original = salida del Poisson; calibrada = tras el calibrador vigente.">
          <ul className="space-y-1 text-sm">
            {cornerModels.map(([market, v]) => (
              <li key={market} className="flex flex-wrap justify-between gap-2">
                <span>{MARKET_DISPLAY_NAMES[market] ?? market}</span>
                <span className="tabular-nums text-slate-300">original {pct(v.model!.original)} · calibrada {pct(v.model!.calibrated)}
                  {v.market && <> · cuota {fmt(v.market.price)} ({pct(v.market.implied)})</>}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Córners propios por equipo">
          <LineTable title="Córners propios" lines={Object.keys(h.team_corners_lines)} home={h.team_corners_lines} away={a.team_corners_lines} />
        </Panel>
        <Panel title="Evolución de córners totales" hint="Descriptiva.">
          <TrendChart home={data.home.analysis.matches} away={data.away.analysis.matches} homeName={data.home.team.name}
                      awayName={data.away.team.name} label="córners totales"
                      metric={r => r.corners_for == null || r.corners_against == null ? null : r.corners_for + r.corners_against} />
        </Panel>
      </div>
    </div>
  );
}

function Shots({ data }: { data: Analysis }) {
  const h = data.home.analysis.profile, a = data.away.analysis.profile;
  if (!h.shots_for.n && !a.shots_for.n) return <EmptyNote>No hay estadísticas de tiros para estos equipos en la muestra elegida.</EmptyNote>;
  return (
    <div className="space-y-3">
      <p className="rounded-lg border border-surface-border bg-surface-alt/40 p-3 text-xs text-slate-300">
        <strong>Análisis estadístico — sin modelo predictivo.</strong> Todavía no existe un modelo validado de tiros: aquí solo hay frecuencias y medias históricas.
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Tiros por partido">
          <SummaryStats label="Tiros" home={h.shots_for} away={a.shots_for} />
          <SummaryStats label="A puerta" home={h.shots_on_target_for} away={a.shots_on_target_for} />
          <SummaryStats label="Fuera" home={h.shots_off_target_for} away={a.shots_off_target_for} />
          <SummaryStats label="Recibidos" home={h.shots_against} away={a.shots_against} />
          <SummaryStats label="A puerta recibidos" home={h.shots_on_target_against} away={a.shots_on_target_against} />
        </Panel>
        <Panel title="Eficacia">
          <TeamLegend home={data.home.team.name} away={data.away.team.name} />
          <CompareBar label="Goles por tiro" home={h.conversion.goals_per_shot} away={a.conversion.goals_per_shot}
                      homeN={h.conversion.n} awayN={a.conversion.n} format={pct} />
          <CompareBar label="Goles por tiro a puerta" home={h.conversion.goals_per_shot_on_target}
                      away={a.conversion.goals_per_shot_on_target} homeN={h.conversion.n} awayN={a.conversion.n} format={pct} />
          <div className="mt-3"><Distribution home={h.shots_total} away={a.shots_total} homeName={data.home.team.name}
                                              awayName={data.away.team.name} unit="tiros totales" /></div>
        </Panel>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Líneas de tiros totales">
          <LineTable title="Tiros totales" lines={Object.keys(h.shots_lines)} home={h.shots_lines} away={a.shots_lines} />
        </Panel>
        <Panel title="Líneas de tiros a puerta totales">
          <LineTable title="A puerta totales" lines={Object.keys(h.shots_on_target_lines)} home={h.shots_on_target_lines} away={a.shots_on_target_lines} />
        </Panel>
      </div>
      <Panel title="Evolución" hint="Descriptiva.">
        <TrendPicker data={data} metrics={[["Tiros", r => r.shots_for], ["Tiros a puerta", r => r.shots_on_target_for],
          ["Tiros recibidos", r => r.shots_against]]} />
      </Panel>
    </div>
  );
}

function HeadToHead({ data }: { data: Analysis }) {
  const { matches, profile } = data.h2h;
  if (!matches.length) return <EmptyNote>No hay enfrentamientos previos en la base de datos.</EmptyNote>;
  const oldest = Math.max(...matches.map(m => m.age_days ?? 0));
  return (
    <div className="space-y-3">
      <Panel title={`Últimos ${matches.length} enfrentamientos`}
             hint={`Visto desde ${data.home.team.name}. El más antiguo es de hace ${Math.round(oldest / 365 * 10) / 10} años: los enfrentamientos lejanos dicen poco del presente y no entran en el modelo.`}
             aside={<SampleBadge n={matches.length} />}>
        <div className="grid grid-cols-3 gap-2 text-center text-sm">
          <div><div className="text-lg font-semibold">{profile.record.W}</div><div className="text-xs text-slate-400">Victorias {data.home.team.name}</div></div>
          <div><div className="text-lg font-semibold">{profile.record.D}</div><div className="text-xs text-slate-400">Empates</div></div>
          <div><div className="text-lg font-semibold">{profile.record.L}</div><div className="text-xs text-slate-400">Victorias {data.away.team.name}</div></div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <span>Goles/partido <strong>{fmt(profile.goals_total.mean)}</strong></span>
          <span>Ambos marcan <strong>{pct(profile.btts.rate)}</strong></span>
          <span>Más de 2,5 <strong>{pct(profile.goals_lines["2.5"].rate)}</strong></span>
          <span>Córners/partido <strong>{fmt(profile.corners_total.mean)}</strong> <SampleBadge n={profile.corners_total.n} /></span>
        </div>
      </Panel>
      <RecentMatches rows={matches} title="Detalle (pulsa para ver estadísticas)" />
    </div>
  );
}

export default function MatchAnalysis({ matchId, competitionName, predictions }: {
  matchId: number; competitionName: string; predictions: React.ReactNode;
}) {
  const [tab, setTab] = useState<Tab>("resumen");
  const [filters, setFilters] = useState<AnalysisFilters>({ window: 10, venue: "split", scope: "all", season: "all" });
  const needsAnalysis = !["predicciones", "cuotas"].includes(tab);
  const key = `analysis:${matchId}:${filters.window}:${filters.venue}:${filters.scope}:${filters.season}`;
  const { data, error, refreshing } = useCachedData<Analysis>(needsAnalysis ? key : null, () => getMatchAnalysis(matchId, filters));
  return (
    <div className="space-y-3">
      <nav aria-label="Secciones del partido" className="-mx-1 flex gap-1 overflow-x-auto border-b border-surface-border px-1">
        {TABS.map(([id, label]) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined}
                  className={`-mb-px shrink-0 border-b-2 px-3 py-2 text-sm ${tab === id ? "border-violet-400 text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
            {label}
          </button>
        ))}
      </nav>
      {needsAnalysis && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Filters value={filters} onChange={setFilters} competition={competitionName} />
          {refreshing && <span className="text-xs text-slate-500">Actualizando…</span>}
        </div>
      )}
      {needsAnalysis && data && <Coverage data={data} />}
      {needsAnalysis && error && <EmptyNote>No se pudo cargar el análisis estadístico.</EmptyNote>}
      {needsAnalysis && !data && !error && <div className="h-48 animate-pulse rounded-lg bg-surface-alt/40" aria-label="Cargando análisis" />}
      {needsAnalysis && data && (
        <>
          {tab === "resumen" && <Summary data={data} />}
          {tab === "estadisticas" && <Stats data={data} />}
          {tab === "goles" && <Goals data={data} />}
          {tab === "corners" && <Corners data={data} />}
          {tab === "tiros" && <Shots data={data} />}
          {tab === "h2h" && <HeadToHead data={data} />}
          <p className="text-xs text-slate-500">{data.temporal_note}</p>
        </>
      )}
      {tab === "predicciones" && predictions}
      {tab === "cuotas" && <OddsCentre matchId={matchId} />}
    </div>
  );
}
