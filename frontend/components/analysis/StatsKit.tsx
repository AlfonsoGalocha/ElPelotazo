"use client";

import Link from "next/link";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import TeamLogo from "@/components/TeamLogo";
import { AWAY_COLOR, HOME_COLOR, fmt, lineLabel, pct, type Describe, type LineFreq, type Rate, type TeamRow } from "@/lib/analysis";

const dateFmt = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", year: "2-digit" });
const tooltipStyle = { background: "#0f172a", borderColor: "#1f2a44", fontSize: 12 };
const axisTick = { fill: "#94a3b8", fontSize: 11 };

export function Panel({ title, hint, children, aside }: { title: string; hint?: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-surface-border bg-card p-3 sm:p-4">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        {aside}
      </div>
      {hint && <p className="mb-2 text-xs text-slate-400">{hint}</p>}
      {children}
    </section>
  );
}

export function SampleBadge({ n, of, label = "partidos" }: { n: number; of?: number; label?: string }) {
  const short = of != null && n < of;
  return (
    <span title={short ? `Solo hay ${n} de los ${of} solicitados con este dato` : `${n} ${label} con este dato`}
          className={`rounded px-1.5 py-0.5 text-[11px] ${short ? "bg-amber-500/10 text-amber-300" : "bg-slate-500/10 text-slate-400"}`}>
      n={n}{of != null && n !== of ? `/${of}` : ""}
    </span>
  );
}

/** Two-sided horizontal comparison: home grows to the left, away to the right, from a shared scale. */
export function CompareBar({ label, home, away, homeN, awayN, digits = 2, format, higherIsBetter = true }: {
  label: string; home: number | null; away: number | null; homeN?: number; awayN?: number; digits?: number;
  format?: (v: number | null) => string; higherIsBetter?: boolean;
}) {
  const show = format ?? ((v: number | null) => fmt(v, digits));
  const scale = Math.max(home ?? 0, away ?? 0) || 1;
  const better = home == null || away == null || home === away ? null : (home > away) === higherIsBetter ? "home" : "away";
  return (
    <div className="grid grid-cols-[3.5rem_1fr_auto_1fr_3.5rem] items-center gap-2 py-1 text-sm">
      <span className={`text-right tabular-nums ${better === "home" ? "font-semibold text-slate-100" : "text-slate-300"}`}>{show(home)}</span>
      <div className="flex h-2 justify-end rounded bg-surface-alt" aria-hidden>
        {home != null && <div className="h-2 rounded-l" style={{ width: `${(home / scale) * 100}%`, background: HOME_COLOR }} />}
      </div>
      <span className="min-w-[7rem] text-center text-xs text-slate-400">
        {label}
        {(homeN != null || awayN != null) && <span className="block text-[10px] text-slate-500">n {homeN ?? "—"} · {awayN ?? "—"}</span>}
      </span>
      <div className="flex h-2 rounded bg-surface-alt" aria-hidden>
        {away != null && <div className="h-2 rounded-r" style={{ width: `${(away / scale) * 100}%`, background: AWAY_COLOR }} />}
      </div>
      <span className={`tabular-nums ${better === "away" ? "font-semibold text-slate-100" : "text-slate-300"}`}>{show(away)}</span>
    </div>
  );
}

export const describeBar = (label: string, home: Describe, away: Describe, higherIsBetter = true) => (
  <CompareBar key={label} label={label} home={home.mean} away={away.mean} homeN={home.n} awayN={away.n} higherIsBetter={higherIsBetter} />
);
export const rateBar = (label: string, home: Rate, away: Rate, higherIsBetter = true) => (
  <CompareBar key={label} label={label} home={home.rate} away={away.rate} homeN={home.n} awayN={away.n} format={pct}
              higherIsBetter={higherIsBetter} />
);

export function TeamLegend({ home, away }: { home: string; away: string }) {
  return (
    <div className="mb-2 flex justify-between text-xs text-slate-400">
      <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: HOME_COLOR }} />{home}</span>
      <span className="flex items-center gap-1.5">{away}<span className="h-2 w-2 rounded-full" style={{ background: AWAY_COLOR }} /></span>
    </div>
  );
}

type ModelCell = { probability: number | null; implied: number | null } | undefined;

/** A. home frequency · B. away frequency · C. model probability · D. implied probability — never mixed. */
export function LineTable({ title, lines, home, away, model }: {
  title: string; lines: string[]; home: LineFreq; away: LineFreq; model?: (line: string) => ModelCell;
}) {
  const hasModel = model && lines.some(line => model(line)?.probability != null || model(line)?.implied != null);
  return (
    <div className="overflow-x-auto">
      <table className="w-full whitespace-nowrap text-sm">
        <thead className="text-xs text-slate-400"><tr>
          <th scope="col" className="px-2 py-1.5 text-left">{title}</th>
          <th scope="col" className="px-2 py-1.5 text-right" title="Frecuencia histórica del local en la muestra elegida">A · Local</th>
          <th scope="col" className="px-2 py-1.5 text-right" title="Frecuencia histórica del visitante en la muestra elegida">B · Visitante</th>
          {hasModel && <th scope="col" className="px-2 py-1.5 text-right" title="Probabilidad predictiva del modelo para este partido">C · Modelo</th>}
          {hasModel && <th scope="col" className="px-2 py-1.5 text-right" title="1 / cuota de consenso; incluye el margen de la casa">D · Cuota</th>}
        </tr></thead>
        <tbody>
          {lines.map(line => {
            const h = home[line], a = away[line], m = model?.(line);
            return (
              <tr key={line} className="border-t border-surface-border">
                <th scope="row" className="px-2 py-1.5 text-left font-normal">Más de {lineLabel(line)}</th>
                <td className="px-2 py-1.5 text-right tabular-nums">{h?.n ? <>{pct(h.rate)} <span className="text-xs text-slate-500">{h.over}/{h.n}</span></> : "—"}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{a?.n ? <>{pct(a.rate)} <span className="text-xs text-slate-500">{a.over}/{a.n}</span></> : "—"}</td>
                {hasModel && <td className="px-2 py-1.5 text-right tabular-nums text-violet-300">{pct(m?.probability)}</td>}
                {hasModel && <td className="px-2 py-1.5 text-right tabular-nums">{pct(m?.implied)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function SummaryStats({ label, home, away }: { label: string; home: Describe; away: Describe }) {
  const cells = (d: Describe) => [fmt(d.mean), fmt(d.median, 1), fmt(d.std), d.min == null ? "—" : `${d.min}–${d.max}`];
  return (
    <div className="overflow-x-auto">
      <table className="w-full whitespace-nowrap text-sm">
        <thead className="text-xs text-slate-400"><tr>
          <th scope="col" className="px-2 py-1.5 text-left">{label}</th>
          {["Media", "Mediana", "Desv. típica", "Rango", "n"].map(h => <th key={h} scope="col" className="px-2 py-1.5 text-right">{h}</th>)}
        </tr></thead>
        <tbody>
          {([["Local", home, HOME_COLOR], ["Visitante", away, AWAY_COLOR]] as const).map(([name, d, color]) => (
            <tr key={name} className="border-t border-surface-border">
              <th scope="row" className="px-2 py-1.5 text-left font-normal">
                <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: color }} />{name}
              </th>
              {cells(d).map((v, i) => <td key={i} className="px-2 py-1.5 text-right tabular-nums">{v}</td>)}
              <td className="px-2 py-1.5 text-right"><SampleBadge n={d.n} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Distribution of a per-match count for both teams, aligned on the same integer axis. */
export function Distribution({ home, away, homeName, awayName, unit }: {
  home: Describe; away: Describe; homeName: string; awayName: string; unit: string;
}) {
  const present = [...Object.keys(home.distribution), ...Object.keys(away.distribution)].map(Number);
  if (!present.length) return <EmptyNote>Sin datos suficientes para la distribución.</EmptyNote>;
  // Continuous integer axis: values never observed show as an empty slot, not as a skipped tick.
  const keys = Array.from({ length: Math.max(...present) - Math.min(...present) + 1 }, (_, i) => Math.min(...present) + i);
  const data = keys.map(k => ({ value: k, home: home.distribution[k] ?? 0, away: away.distribution[k] ?? 0 }));
  return (
    <div className="h-44">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }} barGap={2}>
          <CartesianGrid stroke="#1f2a44" vertical={false} />
          <XAxis dataKey="value" tick={axisTick} tickLine={false} axisLine={{ stroke: "#1f2a44" }} />
          <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#ffffff08" }}
                   labelFormatter={v => `${v} ${unit}`} formatter={(v: number, name: string) => [`${v} partidos`, name]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="home" name={homeName} fill={HOME_COLOR} radius={[4, 4, 0, 0]} />
          <Bar dataKey="away" name={awayName} fill={AWAY_COLOR} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Per-match evolution, oldest to newest; descriptive only. */
export function TrendChart({ home, away, homeName, awayName, metric, label }: {
  home: TeamRow[]; away: TeamRow[]; homeName: string; awayName: string;
  metric: (row: TeamRow) => number | null; label: string;
}) {
  const length = Math.max(home.length, away.length);
  if (!length) return <EmptyNote>Sin partidos previos.</EmptyNote>;
  const h = [...home].reverse(), a = [...away].reverse();
  const data = Array.from({ length }, (_, i) => ({
    index: i + 1 - length, home: h[i - (length - h.length)] ? metric(h[i - (length - h.length)]) : null,
    away: a[i - (length - a.length)] ? metric(a[i - (length - a.length)]) : null,
  }));
  return (
    <div className="h-44">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid stroke="#1f2a44" vertical={false} />
          <XAxis dataKey="index" tick={axisTick} tickLine={false} axisLine={{ stroke: "#1f2a44" }}
                 tickFormatter={v => (v === 0 ? "último" : String(v))} />
          <YAxis allowDecimals={false} tick={axisTick} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} labelFormatter={v => (v === 0 ? "Último partido" : `${-v} partidos antes del último`)}
                   formatter={(v, name) => [v ?? "sin dato", `${name} · ${label}`]} />
          {away.length > 0 && <Legend wrapperStyle={{ fontSize: 11 }} />}
          <Line type="linear" dataKey="home" name={homeName} stroke={HOME_COLOR} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
          {away.length > 0 && <Line type="linear" dataKey="away" name={awayName} stroke={AWAY_COLOR} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const RESULT_LABEL = { W: "W", D: "D", L: "L" } as const;
const RESULT_CLASS = { W: "bg-emerald-500/20 text-emerald-400", D: "bg-slate-500/20 text-slate-300", L: "bg-red-500/20 text-red-400" };

/** Last results, oldest to newest (W/D/L as elsewhere in the app). */
export function FormString({ results }: { results: (string | null)[] }) {
  if (!results.length) return <span className="text-xs text-slate-500">Sin partidos previos</span>;
  return (
    <span className="flex gap-1" aria-label={`Forma: ${results.join(" ")}`}>
      {[...results].reverse().map((r, i) => (
        <span key={i} className={`flex h-5 w-5 items-center justify-center rounded text-[11px] font-semibold ${r ? RESULT_CLASS[r as "W"] : "bg-slate-500/10"}`}>{r ?? "?"}</span>
      ))}
    </span>
  );
}

/** Recent matches with an expandable statistics row; missing values show as «—», never 0. */
export function RecentMatches({ rows, title }: { rows: TeamRow[]; title: string }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!rows.length) return <EmptyNote>Sin partidos en esta muestra.</EmptyNote>;
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h4>
      <ul className="divide-y divide-surface-border rounded border border-surface-border">
        {rows.map(row => (
          <li key={row.match_id}>
            <button type="button" onClick={() => setOpen(open === row.match_id ? null : row.match_id)} aria-expanded={open === row.match_id}
                    className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm hover:bg-surface-alt/60">
              <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded text-[11px] font-semibold ${row.result ? RESULT_CLASS[row.result] : ""}`}>
                {row.result ? RESULT_LABEL[row.result] : "?"}
              </span>
              <span className="w-14 shrink-0 text-xs text-slate-500">{dateFmt.format(new Date(row.kickoff))}</span>
              <span className="w-4 shrink-0 text-xs text-slate-500" title={row.venue === "home" ? "En casa" : "Fuera"}>{row.venue === "home" ? "C" : "F"}</span>
              <TeamLogo name={row.opponent} logoUrl={row.opponent_logo} size="sm" />
              <span className="min-w-0 flex-1 truncate">{row.opponent}</span>
              <span className="shrink-0 font-semibold tabular-nums">{row.goals_for ?? "—"}–{row.goals_against ?? "—"}</span>
            </button>
            {open === row.match_id && (
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 px-3 pb-2 text-xs text-slate-400 sm:grid-cols-4">
                <span>{row.competition_name}</span>
                <span>Descanso {row.goals_ht_for ?? "—"}–{row.goals_ht_against ?? "—"}</span>
                <span>Córners {row.corners_for ?? "—"}–{row.corners_against ?? "—"}</span>
                <span>Tiros {row.shots_for ?? "—"}–{row.shots_against ?? "—"}</span>
                <span>A puerta {row.shots_on_target_for ?? "—"}–{row.shots_on_target_against ?? "—"}</span>
                {row.possession_for != null && <span>Posesión {row.possession_for} %</span>}
                {row.age_days != null && <span>Hace {row.age_days} días</span>}
                <Link href={`/matches/${row.match_id}`} className="text-violet-300 hover:underline">Ver partido</Link>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="rounded border border-dashed border-surface-border px-3 py-4 text-center text-sm text-slate-400">{children}</p>;
}

export function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex overflow-hidden rounded-md border border-surface-border text-xs">
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" aria-pressed={value === v} onClick={() => onChange(v)}
                className={`px-2.5 py-1.5 ${value === v ? "bg-accent/30 text-slate-100" : "text-slate-400 hover:bg-surface-alt"}`}>
          {text}
        </button>
      ))}
    </div>
  );
}
