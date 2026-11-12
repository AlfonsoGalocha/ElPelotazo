"use client";

import { useState } from "react";
import Link from "next/link";
import type { HistoryPrediction, MatchHistory } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";

function ResultBadge({ prediction }: { prediction: HistoryPrediction | undefined }) {
  if (!prediction || prediction.is_correct === null) {
    return <span className="text-xs text-slate-500">sin resolver</span>;
  }
  return prediction.is_correct ? (
    <span className="text-xs font-semibold text-emerald-400">ACERTADA ✓</span>
  ) : (
    <span className="text-xs font-semibold text-red-400">FALLADA ✕</span>
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
// recientes por fecha tal cual). Filtro de liga: de EXCLUSION (todas
// seleccionadas por defecto), al contrario que en Home/Modelo -- aqui lo
// util por defecto es ver la ultima jornada de TODAS las ligas a la vez.
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
  const allCodes = groups.map((g) => g.code);
  const [selected, setSelected] = useState<string[]>(allCodes);

  function toggle(code: string) {
    setSelected((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));
  }

  const allSelected = selected.length === allCodes.length;
  const visibleGroups = groups.filter((g) => selected.includes(g.code));

  return (
    <div className="flex flex-col gap-6">
      {groups.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Ligas:</span>
          <button
            onClick={() => setSelected(allCodes)}
            className={
              "rounded-full border px-3 py-1 " +
              (allSelected
                ? "border-slate-400 bg-slate-700 text-slate-100"
                : "border-surface-border text-slate-400 hover:bg-surface-raised")
            }
          >
            Todas
          </button>
          {groups.map((g) => (
            <button
              key={g.code}
              onClick={() => toggle(g.code)}
              className={
                "rounded-full border px-3 py-1 " +
                (selected.includes(g.code)
                  ? "border-slate-400 bg-slate-700 text-slate-100"
                  : "border-surface-border text-slate-500 hover:bg-surface-raised")
              }
            >
              {g.name}
            </button>
          ))}
        </div>
      )}

      {visibleGroups.length === 0 && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-6 text-sm text-slate-500">
          No hay ninguna liga seleccionada.
        </div>
      )}

      {visibleGroups.map((group) => (
        <div key={group.code}>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {group.matchday !== null ? `Jornada ${group.matchday}` : "Últimos jugados"} · {group.name}
          </h2>
          <div className="overflow-hidden rounded-lg border border-surface-border">
            <table className="w-full text-sm">
              <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
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
                        <Link href={`/history/${row.match.id}`} className="text-slate-200 hover:underline">
                          {row.match.home_team.canonical_name} vs {row.match.away_team.canonical_name}
                        </Link>
                        <div className="text-[11px] text-slate-500">
                          {new Date(row.match.kickoff_utc).toLocaleDateString("es-ES", {
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
