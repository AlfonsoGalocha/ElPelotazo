"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle2, MinusCircle, XCircle } from "lucide-react";
import type { HistoryPrediction, MatchHistory } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { getHistory } from "@/lib/api";
import { formatMatchDateOnly, seasonLabelFor } from "@/lib/dates";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
import TeamLogo from "./TeamLogo";
import { EmptyState } from "./EmptyState";

// Colores pedidos por el usuario para "Rendimiento del modelo": verde
// acertada, rojo fallida, GRIS pendiente O anulada (mismo color, texto
// distinto para no confundirlas), amarillo media ganancia/media perdida
// (half_won/half_lost -- ningun mercado real los produce todavia, ver
// architecture_audit.md, pero el badge ya sabe pintarlos si algun dia
// los hay).
function ResultBadge({ prediction }: { prediction: HistoryPrediction | undefined }) {
  if (!prediction) {
    return <span className="text-xs text-slate-500">sin resolver</span>;
  }
  if (prediction.settlement_status === "void") {
    return (
      <span className="flex items-center gap-1 text-xs font-semibold text-slate-500">
        <MinusCircle className="h-3.5 w-3.5" /> ANULADA
      </span>
    );
  }
  if (prediction.settlement_status === "half_won" || prediction.settlement_status === "half_lost") {
    return (
      <span className="flex items-center gap-1 text-xs font-semibold text-amber-400">
        {prediction.settlement_status === "half_won" ? "MEDIA GANANCIA" : "MEDIA PÉRDIDA"}
      </span>
    );
  }
  if (prediction.settlement_verification === "unverified" || prediction.settlement_verification === "disputed") {
    return <span className="text-xs text-slate-400">{prediction.settlement_verification === "disputed" ? "Liquidación discrepante" : "Córners sin verificar"}</span>;
  }
  if (prediction.is_correct === null) {
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

// Fila de partido compartida entre la vista por defecto ("Todas" las
// ligas, su ultima jornada) y la vista de exploracion por
// temporada/jornada (una sola liga) -- misma tabla, nunca duplicada.
function HistoryMatchRow({ row }: { row: MatchHistory }) {
  const best = bestPrediction(row);
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
        {best
          ? MARKET_DISPLAY_NAMES[best.market] ?? best.market
          : row.predictions.length === 0
            ? "Sin predicción (se descubrió ya jugado)"
            : "—"}
      </td>
      <td className="px-3 py-2 text-slate-300">{best ? `${(best.model_probability * 100).toFixed(1)}%` : "—"}</td>
      <td className="px-3 py-2 text-slate-300">{best?.market_odds != null ? best.market_odds.toFixed(2) : "—"}</td>
      <td className="px-3 py-2 text-slate-300">
        {best?.expected_value != null ? (
          <span className={best.expected_value >= 0 ? "text-edge-positive" : "text-edge-negative"}>
            {best.expected_value >= 0 ? "+" : ""}
            {(best.expected_value * 100).toFixed(1)}%
          </span>
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-2">
        <ResultBadge prediction={best} />
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
            <th className="px-3 py-2">Mejor predicción</th>
            <th className="px-3 py-2">Modelo</th>
            <th className="px-3 py-2">Cuota</th>
            <th className="px-3 py-2">EV</th>
            <th className="px-3 py-2">Estado</th>
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
