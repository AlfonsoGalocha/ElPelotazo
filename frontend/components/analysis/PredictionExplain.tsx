"use client";

import { useCachedData } from "@/lib/clientCache";
import { fmt, getMatchAnalysis, pct, type MatchAnalysis, type Profile } from "@/lib/analysis";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { EmptyNote, Panel } from "./StatsKit";

const FILTERS = { window: 10, venue: "split", scope: "all", season: "all" } as const;
const when = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const EVIDENCE: Record<string, string> = {
  competition_market: "esta competición y mercado", competition_market_shrunk: "esta competición, mezclada con un respaldo",
  market_tier: "competiciones del mismo nivel", market_global: "todas las competiciones",
};

/** Descriptive facts from real samples for a market; never presented as causes of the prediction. */
function context(market: string, home: Profile, away: Profile, names: [string, string]): string[] {
  const line = (freq: Profile["goals_lines"], key: string, who: string, what: string) => {
    const row = freq[key];
    return row?.n ? `${who} superó ${what} en ${row.over} de sus últimos ${row.n} partidos con dato.` : null;
  };
  const goals = market.match(/^over_(\d)_5$/) ?? market.match(/^under_(\d)_5$/);
  const corners = market.match(/^corners_(?:over|under)_(\d+)_5$/);
  const team = market.match(/^(home|away)_team_over_(\d)_5$/);
  const facts: (string | null)[] = [];
  if (goals) {
    const key = `${goals[1]}.5`;
    facts.push(line(home.goals_lines, key, names[0], `los ${goals[1]},5 goles`), line(away.goals_lines, key, names[1], `los ${goals[1]},5 goles`));
    facts.push(`Media de goles totales: ${names[0]} ${fmt(home.goals_total.mean)} · ${names[1]} ${fmt(away.goals_total.mean)}.`);
  } else if (corners) {
    const key = `${corners[1]}.5`;
    facts.push(line(home.corners_lines, key, names[0], `los ${corners[1]},5 córners`), line(away.corners_lines, key, names[1], `los ${corners[1]},5 córners`));
    facts.push(`Media de córners totales: ${names[0]} ${fmt(home.corners_total.mean)} (n=${home.corners_total.n}) · ${names[1]} ${fmt(away.corners_total.mean)} (n=${away.corners_total.n}).`);
  } else if (team) {
    const profile = team[1] === "home" ? home : away, name = team[1] === "home" ? names[0] : names[1];
    facts.push(line(profile.team_goals_lines, `${team[2]}.5`, name, `${Number(team[2]) + 0.5 > 1 ? `los ${team[2]},5 goles propios` : "el 0,5 de goles propios"}`));
    facts.push(`${name} marca ${fmt(profile.goals_for.mean)} goles por partido de media.`);
  } else if (market === "btts") {
    facts.push(`Ambos marcaron en el ${pct(home.btts.rate)} de los partidos de ${names[0]} (${home.btts.count}/${home.btts.n}) y en el ${pct(away.btts.rate)} de los de ${names[1]} (${away.btts.count}/${away.btts.n}).`);
  } else if (["home_win", "away_win", "draw", "double_chance_1x", "double_chance_x2", "double_chance_12"].includes(market)) {
    facts.push(`${names[0]}: ${home.record.W}V ${home.record.D}E ${home.record.L}D en la muestra · ${names[1]}: ${away.record.W}V ${away.record.D}E ${away.record.L}D.`);
    facts.push(`Goles a favor/en contra: ${names[0]} ${fmt(home.goals_for.mean)}/${fmt(home.goals_against.mean)} · ${names[1]} ${fmt(away.goals_for.mean)}/${fmt(away.goals_against.mean)}.`);
  }
  return facts.filter((f): f is string => !!f);
}

export default function PredictionExplain({ matchId, predictions }: { matchId: number; predictions: Prediction[] }) {
  const key = `analysis:${matchId}:${FILTERS.window}:${FILTERS.venue}:${FILTERS.scope}:${FILTERS.season}`;
  const { data } = useCachedData<MatchAnalysis>(key, () => getMatchAnalysis(matchId, FILTERS));
  if (!predictions.length) return <EmptyNote>Sin predicciones guardadas para explicar.</EmptyNote>;
  const names: [string, string] = data ? [data.home.team.name, data.away.team.name] : ["Local", "Visitante"];
  const ordered = [...predictions].sort((a, b) => (b.calibrated_probability ?? b.model_probability) - (a.calibrated_probability ?? a.model_probability));
  return (
    <Panel title="Explicación de las predicciones"
           hint="El contexto estadístico describe la muestra reciente (últimos 10, local en casa y visitante fuera); no demuestra por qué el modelo da esa probabilidad. Las contribuciones de variables solo aparecen cuando el modelo permite calcularlas.">
      <div className="space-y-2">
        {ordered.map(p => {
          const implied = p.market_odds ? 1 / p.market_odds : null;
          const probability = p.calibrated_probability ?? p.model_probability;
          const ev = p.market_odds ? probability * p.market_odds - 1 : null;
          const evidence = data?.markets[p.market]?.evidence;
          const facts = data ? context(p.market, data.home.analysis.profile, data.away.analysis.profile, names) : [];
          return (
            <details key={p.id} className="rounded border border-surface-border">
              <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <span className="font-medium">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</span>
                <span className="tabular-nums text-slate-300">
                  <span className="text-violet-300">{pct(probability)}</span>
                  {p.market_odds && <> · cuota {fmt(p.market_odds)} · implícita {pct(implied)}</>}
                  {ev != null && <span className={ev > 0 ? "text-emerald-400" : "text-slate-400"}> · EV {ev > 0 ? "+" : ""}{(ev * 100).toFixed(1)} %</span>}
                </span>
              </summary>
              <div className="grid gap-3 border-t border-surface-border px-3 py-2 text-xs text-slate-300 md:grid-cols-2">
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                  <dt className="text-slate-500">Probabilidad original</dt><dd>{pct(p.model_probability)}</dd>
                  <dt className="text-slate-500">Calibrada</dt>
                  <dd>{p.calibrated_probability == null ? "Sin calibración validada: se usa la original" : pct(p.calibrated_probability)}</dd>
                  <dt className="text-slate-500">Incertidumbre</dt>
                  <dd>{p.uncertainty_low != null && p.uncertainty_high != null ? `${pct(p.uncertainty_low)} – ${pct(p.uncertainty_high)}` : "No disponible"}</dd>
                  <dt className="text-slate-500">Prob. de mercado</dt>
                  <dd>{p.market_probability != null ? `${pct(p.market_probability)} (sin margen, ${p.bookmakers_count ?? "?"} casas)` : "—"}</dd>
                  <dt className="text-slate-500">Modelo</dt><dd>Versión #{p.model_version_id}</dd>
                  <dt className="text-slate-500">Actualizada</dt><dd>{when.format(new Date(p.created_at))}</dd>
                  <dt className="text-slate-500">Evidencia histórica</dt>
                  <dd>{evidence ? `${evidence.n_observations} liquidaciones en ${EVIDENCE[evidence.source_level] ?? evidence.source_level} · fiabilidad ${fmt(evidence.value)}` : "Sin historial liquidado para este mercado"}</dd>
                </dl>
                <div className="space-y-2">
                  <div>
                    <p className="mb-1 font-semibold text-slate-400">Contexto estadístico</p>
                    {facts.length ? <ul className="list-disc space-y-0.5 pl-4">{facts.map(f => <li key={f}>{f}</li>)}</ul>
                      : <p className="text-slate-500">{data ? "Sin contexto descriptivo para este mercado." : "Cargando contexto…"}</p>}
                  </div>
                  <div>
                    <p className="mb-1 font-semibold text-slate-400">Variables del modelo</p>
                    {p.explanation.length ? (
                      <ul className="space-y-0.5">
                        {p.explanation.slice(0, 6).map(f => (
                          <li key={f.feature} className="flex justify-between gap-2">
                            <span className="truncate">{f.display_name ?? f.feature}</span>
                            <span className={f.direction === "increases_probability" ? "text-emerald-400" : "text-red-400"}>
                              {f.direction === "increases_probability" ? "▲ sube" : "▼ baja"}</span>
                          </li>
                        ))}
                      </ul>
                    ) : <p className="text-slate-500">Este modelo no expone contribuciones calculables: solo se muestra contexto.</p>}
                  </div>
                </div>
              </div>
            </details>
          );
        })}
      </div>
    </Panel>
  );
}
