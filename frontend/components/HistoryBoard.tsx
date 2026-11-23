"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { MatchHistory, PublishedHistorySelection } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { getHistory } from "@/lib/api";
import { formatMatchDateOnly, seasonLabelFor } from "@/lib/dates";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
import TeamLogo from "./TeamLogo";
import { EmptyState } from "./EmptyState";

function PublishedSelection({ selection, missing, model = false }: {
  selection?: PublishedHistorySelection | null;
  missing: string;
  model?: boolean;
}) {
  if (!selection) return <span className="text-xs text-slate-400">{missing}</span>;
  return (
    <div className="space-y-0.5">
      <div>{MARKET_DISPLAY_NAMES[selection.market] ?? selection.market} · {(selection.probability * 100).toFixed(1)} %</div>
      {selection.odds != null ? (
        <div className="text-xs text-slate-400">
          {model ? "Cuota guardada (consenso)" : "Cuota"} {selection.odds.toFixed(2)}
          {selection.bookmaker ? ` · ${selection.bookmaker}` : ""}
        </div>
      ) : model && <div className="text-xs text-slate-400">Sin cuota registrada</div>}
      {model && selection.market_probability_gap != null && (
        <div className={selection.large_market_gap ? "text-xs text-amber-300" : "text-xs text-slate-400"}>
          {selection.large_market_gap ? "Revisar · " : ""}
          {selection.market_probability_gap > 0 ? "+" : ""}{(selection.market_probability_gap * 100).toFixed(1)} pp frente al mercado
        </div>
      )}
      {selection.ev != null && <div className="text-xs text-slate-400">EV estimado {(selection.ev * 100).toFixed(1)} %</div>}
      <div className={selection.is_correct === true ? "text-xs text-emerald-400" : selection.is_correct === false ? "text-xs text-red-400" : "text-xs text-slate-400"}>
        {selection.is_correct === true ? "Acierto" : selection.is_correct === false ? "Fallo" : "Resultado sin verificar"}
      </div>
    </div>
  );
}

function HistoryMatchRow({ row }: { row: MatchHistory }) {
  return (
    <tr className="border-t border-surface-border">
      <td className="px-3 py-2">
        <Link href={`/history/${row.match.id}`} className="flex items-center gap-1.5 text-slate-200 hover:underline">
          <TeamLogo name={row.match.home_team.canonical_name} logoUrl={row.match.home_team.logo_url} size="sm" />
          {row.match.home_team.canonical_name} vs {row.match.away_team.canonical_name}
          <TeamLogo name={row.match.away_team.canonical_name} logoUrl={row.match.away_team.logo_url} size="sm" />
        </Link>
        <div className="text-[11px] text-slate-500">
          {formatMatchDateOnly(row.match.kickoff_utc, { day: "2-digit", month: "short", year: "numeric" })}
        </div>
      </td>
      <td className="px-3 py-2 font-medium text-slate-200">
        {row.match.home_goals} - {row.match.away_goals}
      </td>
      <td className="px-3 py-2 text-slate-300">
        <PublishedSelection model selection={row.evaluated_model_pick} missing="Sin pronóstico prepartido evaluable" />
      </td>
      <td className="px-3 py-2 text-slate-300">
        <PublishedSelection selection={row.official_recommendation} missing="Sin recomendación registrada" />
      </td>
    </tr>
  );
}

function HistoryMatchTable({ rows }: { rows: MatchHistory[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-surface-border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-surface-alt/60 text-left text-[11px] uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">Partido</th>
            <th className="px-3 py-2">Resultado</th>
            <th className="px-3 py-2">Pronóstico evaluado del modelo</th>
            <th className="px-3 py-2">Recomendación oficial</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <HistoryMatchRow key={row.match.id} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface CompetitionGroup {
  code: string;
  name: string;
  matchday: number | null;
  rows: MatchHistory[];
}

// Pedido real del usuario: "quiero que me salga la ultima jornada de cada
// liga" -- agrupa por competicion y, dentro de cada una, se queda SOLO
// con la jornada mas alta que tenga partidos finalizados (si ninguno
// trae `matchday`, p.ej. historico muy antiguo, se queda con los mas
// recientes por fecha tal cual).
function groupByCompetitionLatestMatchday(rows: MatchHistory[]): CompetitionGroup[] {
  const byCode = new Map<string, MatchHistory[]>();
  for (const row of rows) {
    const code = row.match.competition.code;
    const list = byCode.get(code) ?? [];
    list.push(row);
    byCode.set(code, list);
  }
  const groups: CompetitionGroup[] = [];
  for (const [code, groupRows] of byCode) {
    const matchdays = groupRows.map((r) => r.match.matchday).filter((m): m is number => m !== null);
    const maxMatchday = matchdays.length > 0 ? Math.max(...matchdays) : null;
    const filtered = maxMatchday !== null ? groupRows.filter((r) => r.match.matchday === maxMatchday) : groupRows;
    groups.push({ code, name: groupRows[0].match.competition.name, matchday: maxMatchday, rows: filtered });
  }
  return groups.sort((a, b) => a.name.localeCompare(b.name));
}

export default function HistoryBoard({ rows, simple = false }: { rows: MatchHistory[]; simple?: boolean }) {
  const groups = groupByCompetitionLatestMatchday(rows);
  // Pedido real del usuario: "ponme el mismo filtro de oportunidades en
  // todas las secciones de la app" -- un unico desplegable con optgroup
  // Ligas/Selecciones, "Todas" por defecto (nunca botones sueltos de
  // multi-seleccion).
  const [selected, setSelected] = useState<string | null>(null);
  // Subseccion "Jornadas" de "Rendimiento del modelo" (pedido real del
  // usuario: "Selector de competición. Selector de temporada. Selector
  // de jornada"): solo tiene sentido elegir temporada/jornada sobre UNA
  // liga concreta (con "Todas" seleccionadas se ve la ultima jornada de
  // cada una, igual que antes). Al elegir una, se trae un historico
  // PROFUNDO solo de esa competicion (los `rows` iniciales ya descartaron
  // todo lo que no fuera la ultima jornada de cada liga) -- nunca se
  // vuelve a pedir nada mientras siga "Todas".
  const [deepRows, setDeepRows] = useState<MatchHistory[] | null>(null);
  const [deepLoading, setDeepLoading] = useState(false);
  const [season, setSeason] = useState<string | null>(null);
  const [matchday, setMatchday] = useState<number | null>(null);

  useEffect(() => {
    if (!selected) {
      setDeepRows(null);
      setSeason(null);
      setMatchday(null);
      return;
    }
    setDeepLoading(true);
    setSeason(null);
    setMatchday(null);
    getHistory(selected, 500, simple)
      .then(setDeepRows)
      .catch(() => setDeepRows([]))
      .finally(() => setDeepLoading(false));
  }, [selected, simple]);

  const clubGroups = groups.filter((g) => !isInternationalCompetition(g.code));
  const internationalGroups = groups.filter((g) => isInternationalCompetition(g.code));

  const seasons = deepRows
    ? [...new Set(deepRows.map((r) => seasonLabelFor(r.match.kickoff_utc)))].sort().reverse()
    : [];
  const effectiveSeason = season ?? seasons[0] ?? null;
  const rowsInSeason = deepRows
    ? deepRows.filter((r) => seasonLabelFor(r.match.kickoff_utc) === effectiveSeason)
    : [];
  const matchdaysInSeason = [
    ...new Set(rowsInSeason.map((r) => r.match.matchday).filter((m): m is number => m !== null)),
  ].sort((a, b) => b - a);
  const effectiveMatchday = matchday ?? matchdaysInSeason[0] ?? null;
  const deepVisibleRows =
    matchdaysInSeason.length > 0 ? rowsInSeason.filter((r) => r.match.matchday === effectiveMatchday) : rowsInSeason;

  const visibleGroups = selected ? [] : groups;

  return (
    <div className="flex flex-col gap-6">
      {(groups.length > 1 || (simple && groups.length > 0)) && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Liga:</span>
          <select
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value || null)}
            className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
          >
            <option value="">Todas (última jornada de cada una)</option>
            <optgroup label="Ligas">
              {clubGroups.map((g) => (
                <option key={g.code} value={g.code}>
                  {g.name}
                </option>
              ))}
            </optgroup>
            {internationalGroups.length > 0 && (
              <optgroup label="Selecciones">
                {internationalGroups.map((g) => (
                  <option key={g.code} value={g.code}>
                    {g.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>

          {!simple && selected && seasons.length > 0 && (
            <>
              <span className="ml-2 text-slate-500">Temporada:</span>
              <select
                value={effectiveSeason ?? ""}
                onChange={(e) => {
                  setSeason(e.target.value);
                  setMatchday(null); // la jornada elegida puede no existir en la nueva temporada
                }}
                className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
              >
                {seasons.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </>
          )}

          {selected && matchdaysInSeason.length > 0 && (
            <>
              <span className="ml-2 text-slate-500">Jornada:</span>
              <select
                value={effectiveMatchday ?? ""}
                onChange={(e) => setMatchday(Number(e.target.value))}
                className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
              >
                {matchdaysInSeason.map((m) => (
                  <option key={m} value={m}>
                    Jornada {m}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      )}

      {!selected && visibleGroups.length === 0 && (
        <EmptyState title="Sin partidos jugados todavía para esta liga." />
      )}

      {!selected &&
        visibleGroups.map((group) => (
          <div key={group.code}>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
              {group.matchday !== null ? `Jornada ${group.matchday}` : "Últimos jugados"} · {group.name}
            </h2>
            <HistoryMatchTable rows={group.rows} />
          </div>
        ))}

      {selected && deepLoading && (
        <div className="h-32 animate-pulse rounded-xl border border-surface-border bg-card" />
      )}

      {selected && !deepLoading && deepRows !== null && deepVisibleRows.length === 0 && (
        <EmptyState title="Sin partidos jugados todavía para esta liga." />
      )}

      {selected && !deepLoading && deepVisibleRows.length > 0 && (
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {matchdaysInSeason.length > 0 ? `Jornada ${effectiveMatchday}` : "Todos los partidos"} ·{" "}
            {effectiveSeason}
          </h2>
          <HistoryMatchTable rows={deepVisibleRows} />
        </div>
      )}
    </div>
  );
}
