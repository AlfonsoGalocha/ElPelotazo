"use client";

import Link from "next/link";
import { useState } from "react";
import TeamLogo from "@/components/TeamLogo";
import { useCachedData } from "@/lib/clientCache";
import { fmt, getTeamProfile, pct, type TeamProfile, type TeamRow } from "@/lib/analysis";
import {
  Distribution, EmptyNote, FormString, LineTable, Panel, RecentMatches, SampleBadge, Segmented, TeamLegend, TrendChart,
  describeBar, rateBar,
} from "./StatsKit";

const when = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const TRENDS: [string, (r: TeamRow) => number | null][] = [
  ["Goles marcados", r => r.goals_for], ["Goles recibidos", r => r.goals_against], ["Córners a favor", r => r.corners_for],
  ["Tiros", r => r.shots_for], ["Tiros a puerta", r => r.shots_on_target_for],
];

export default function TeamProfileView({ teamId }: { teamId: number }) {
  const [window, setWindow] = useState(10);
  const [competition, setCompetition] = useState<string | undefined>();
  const [trend, setTrend] = useState(0);
  const { data, error } = useCachedData<TeamProfile>(`team:${teamId}:${window}:${competition ?? ""}`, () => getTeamProfile(teamId, window, competition));
  if (error) return <EmptyNote>No se pudo cargar el equipo.</EmptyNote>;
  if (!data) return <div className="h-64 animate-pulse rounded-lg bg-surface-alt/40" aria-label="Cargando equipo" />;
  const o = data.overall.profile, h = data.home.profile, a = data.away.profile;
  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center gap-3 rounded-xl border border-surface-border bg-card px-4 py-3">
        <TeamLogo name={data.team.name} logoUrl={data.team.logo_url} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold">{data.team.name}</h1>
          <div className="flex flex-wrap gap-2 text-xs text-slate-400">
            {data.standings.map(s => <span key={s.competition}>{s.name}: {s.position}.º · {s.points} pts ({s.played} PJ)</span>)}
            {!data.standings.length && <span>{data.competitions.map(c => c.name).join(" · ")}</span>}
          </div>
        </div>
        <FormString results={data.recent.slice(0, 5).map(r => r.result)} />
      </header>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Ventana" value={window} options={[[5, "Últ. 5"], [10, "Últ. 10"], [20, "Últ. 20"]]} onChange={setWindow} />
        <select aria-label="Competición" value={competition ?? ""} onChange={e => setCompetition(e.target.value || undefined)}
                className="rounded border border-surface-border bg-surface-raised px-2 py-1.5 text-xs">
          <option value="">Todas las competiciones</option>
          {data.competitions.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
        </select>
        <SampleBadge n={o.matches} of={window} />
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        <Panel title="Rendimiento general">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
            <dt className="text-slate-400">Balance</dt><dd>{o.record.W}V {o.record.D}E {o.record.L}D</dd>
            <dt className="text-slate-400">Goles a favor</dt><dd>{fmt(o.goals_for.mean)} <SampleBadge n={o.goals_for.n} /></dd>
            <dt className="text-slate-400">Goles en contra</dt><dd>{fmt(o.goals_against.mean)}</dd>
            <dt className="text-slate-400">Córners a favor</dt><dd>{fmt(o.corners_for.mean)} <SampleBadge n={o.corners_for.n} /></dd>
            <dt className="text-slate-400">Tiros / a puerta</dt><dd>{fmt(o.shots_for.mean)} / {fmt(o.shots_on_target_for.mean)} <SampleBadge n={o.shots_for.n} /></dd>
            <dt className="text-slate-400">Ambos marcan</dt><dd>{pct(o.btts.rate)}</dd>
            <dt className="text-slate-400">Portería a cero</dt><dd>{pct(o.clean_sheets.rate)}</dd>
          </dl>
        </Panel>
        <Panel title="En casa frente a fuera" hint="Misma ventana aplicada a cada condición.">
          <TeamLegend home={`En casa (${h.matches})`} away={`Fuera (${a.matches})`} />
          {describeBar("Goles a favor", h.goals_for, a.goals_for)}
          {describeBar("Goles en contra", h.goals_against, a.goals_against, false)}
          {describeBar("Córners a favor", h.corners_for, a.corners_for)}
          {describeBar("Tiros", h.shots_for, a.shots_for)}
          {describeBar("Tiros a puerta", h.shots_on_target_for, a.shots_on_target_for)}
          {rateBar("Ambos marcan", h.btts, a.btts)}
        </Panel>
        <Panel title="Próximos partidos">
          {data.upcoming.length ? (
            <ul className="space-y-1.5 text-sm">
              {data.upcoming.map(m => (
                <li key={m.id}>
                  <Link href={`/matches/${m.id}`} className="hover:underline">{m.home.name} – {m.away.name}</Link>
                  <span className="block text-xs text-slate-500">{when.format(new Date(m.kickoff))} · {m.competition}</span>
                </li>
              ))}
            </ul>
          ) : <EmptyNote>Sin partidos programados.</EmptyNote>}
        </Panel>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Líneas de goles (frecuencia)" hint="A = en casa · B = fuera.">
          <LineTable title="Goles totales" lines={Object.keys(h.goals_lines)} home={h.goals_lines} away={a.goals_lines} />
        </Panel>
        <Panel title="Líneas de córners (frecuencia)" hint="A = en casa · B = fuera.">
          <LineTable title="Córners totales" lines={Object.keys(h.corners_lines)} home={h.corners_lines} away={a.corners_lines} />
        </Panel>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Distribución de goles totales">
          <Distribution home={h.goals_total} away={a.goals_total} homeName="En casa" awayName="Fuera" unit="goles" />
        </Panel>
        <Panel title="Distribución de córners totales">
          <Distribution home={h.corners_total} away={a.corners_total} homeName="En casa" awayName="Fuera" unit="córners" />
        </Panel>
      </div>
      <Panel title="Tendencia de temporada" hint="Últimos 40 partidos; descriptiva, no predictiva.">
        <div className="mb-2"><Segmented label="Métrica" value={trend} options={TRENDS.map(([n], i) => [i, n] as [number, string])} onChange={setTrend} /></div>
        <TrendChart home={data.recent} away={[]} homeName={data.team.name} awayName="" metric={TRENDS[trend][1]} label={TRENDS[trend][0]} />
      </Panel>
      <RecentMatches rows={data.recent} title="Historial de partidos" />
    </div>
  );
}
