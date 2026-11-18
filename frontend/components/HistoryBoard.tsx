"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, XCircle } from "lucide-react";
import type { HistoryPrediction, MatchHistory } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { formatMatchDateOnly } from "@/lib/dates";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
import TeamLogo from "./TeamLogo";
import { EmptyState } from "./EmptyState";

function ResultBadge({ prediction }: { prediction: HistoryPrediction | undefined }) {
  if (!prediction || prediction.is_correct === null) {
    return <span className="text-xs text-slate-500">sin resolver</span>;
  }
  return prediction.is_correct ? (
    <span className="flex items-center gap-1 text-xs font-semibold text-edge-positive">
      <CheckCircle2 className="h-3.5 w-3.5" /> ACERTADA
    </span>
  ) : (
    <span className="flex items-center gap-1 text-xs font-semibold text-red-400">
      <XCircle className="h-3.5 w-3.5" /> FALLADA
    </span>
  );
}

function bestPrediction(row: MatchHistory): HistoryPrediction | undefined {
  return row.predictions.find((p) => p.id === row.best_prediction_id);
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

export default function HistoryBoard({ rows }: { rows: MatchHistory[] }) {
  const groups = groupByCompetitionLatestMatchday(rows);
  // Pedido real del usuario: "ponme el mismo filtro de oportunidades en
  // todas las secciones de la app" -- un unico desplegable con optgroup
  // Ligas/Selecciones, "Todas" por defecto (nunca botones sueltos de
  // multi-seleccion).
  const [selected, setSelected] = useState<string | null>(null);

  const visibleGroups = selected ? groups.filter((g) => g.code === selected) : groups;
  const clubGroups = groups.filter((g) => !isInternationalCompetition(g.code));
  const internationalGroups = groups.filter((g) => isInternationalCompetition(g.code));

  return (
    <div className="flex flex-col gap-6">
      {groups.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Liga:</span>
          <select
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value || null)}
            className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
          >
            <option value="">Todas</option>
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
        </div>
      )}

      {visibleGroups.length === 0 && (
        <EmptyState title="Sin partidos jugados todavía para esta liga." />
      )}

      {visibleGroups.map((group) => (
        <div key={group.code}>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {group.matchday !== null ? `Jornada ${group.matchday}` : "Últimos jugados"} · {group.name}
          </h2>
          <div className="overflow-hidden rounded-xl border border-surface-border bg-card">
            <table className="w-full text-sm">
              <thead className="bg-surface-alt/60 text-left text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">Partido</th>
                  <th className="px-3 py-2">Resultado</th>
                  <th className="px-3 py-2">Mejor predicción</th>
                  <th className="px-3 py-2">Modelo</th>
                  <th className="px-3 py-2">Cuota</th>
                  <th className="px-3 py-2">Estado</th>
                </tr>
              </thead>
              <tbody>
                {group.rows.map((row) => {
                  const best = bestPrediction(row);
                  return (
                    <tr key={row.match.id} className="border-t border-surface-border">
                      <td className="px-3 py-2">
                        <Link
                          href={`/history/${row.match.id}`}
                          className="flex items-center gap-1.5 text-slate-200 hover:underline"
                        >
                          <TeamLogo
                            name={row.match.home_team.canonical_name}
                            logoUrl={row.match.home_team.logo_url}
                            size="sm"
                          />
                          {row.match.home_team.canonical_name} vs {row.match.away_team.canonical_name}
                          <TeamLogo
                            name={row.match.away_team.canonical_name}
                            logoUrl={row.match.away_team.logo_url}
                            size="sm"
                          />
                        </Link>
                        <div className="text-[11px] text-slate-500">
                          {formatMatchDateOnly(row.match.kickoff_utc, {
                            day: "2-digit",
                            month: "short",
                            year: "numeric",
                          })}
                        </div>
                      </td>
                      <td className="px-3 py-2 font-medium text-slate-200">
                        {row.match.home_goals} - {row.match.away_goals}
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {best
                          ? MARKET_DISPLAY_NAMES[best.market] ?? best.market
                          : row.predictions.length === 0
                            ? "Sin predicción (se descubrió ya jugado)"
                            : "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {best ? `${(best.model_probability * 100).toFixed(1)}%` : "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {best?.market_odds != null ? best.market_odds.toFixed(2) : "—"}
                      </td>
                      <td className="px-3 py-2">
                        <ResultBadge prediction={best} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
