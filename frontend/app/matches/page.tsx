"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getCompetitions, getMatchesList } from "@/lib/api";
import type { Competition, Match } from "@/types";

type StatusFilter = "all" | "scheduled" | "finished";

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Bug real reportado por el usuario: al ver "Jugados" salian partidos de
// temporadas muy antiguas (el historico profundo usado para entrenar).
// Misma convencion de temporada europea que el backend (season_label,
// backend/app/utils/dates.py: julio es el mes de corte) -- nunca una
// ventana arbitraria de dias.
function currentSeasonStartDate(): string {
  const now = new Date();
  const startYear = now.getMonth() + 1 >= 7 ? now.getFullYear() : now.getFullYear() - 1;
  return `${startYear}-07-01`;
}

// Pagina "Partidos" del nuevo menu: listado general con busqueda y
// filtros, algo que antes no existia (solo "jornada actual" en el home o
// el historico de ya jugados) -- reutiliza GET /matches tal cual, que ya
// soportaba competicion/fecha/busqueda/estado como filtros.
export default function MatchesPage() {
  const [competitions, setCompetitions] = useState<Competition[]>([]);
  const [competitionCode, setCompetitionCode] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>("scheduled");
  const [search, setSearch] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompetitions()
      .then(setCompetitions)
      .catch(() => setCompetitions([]));
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const timeout = setTimeout(() => {
      getMatchesList({
        competitionCode: competitionCode ?? undefined,
        status: status === "all" ? undefined : status,
        search: search.trim() || undefined,
        dateFrom: currentSeasonStartDate(),
        limit: 100,
      })
        .then((rows) => {
          // El backend no ordena de forma estable segun el filtro de
          // estado: proximos partidos primero por fecha de kickoff, ya
          // jugados mas reciente primero.
          const sorted = [...rows].sort((a, b) => {
            const diff = new Date(a.kickoff_utc).getTime() - new Date(b.kickoff_utc).getTime();
            return status === "finished" ? -diff : diff;
          });
          setMatches(sorted);
        })
        .catch(() => setError("No se pudo conectar con la API."))
        .finally(() => setLoading(false));
    }, 200);
    return () => clearTimeout(timeout);
  }, [competitionCode, status, search]);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Partidos</h1>
        <p className="text-sm text-slate-500">
          Busca cualquier partido de las 5 ligas, próximo o ya jugado.
        </p>
      </div>

      <div className="flex flex-col gap-3 rounded-lg border border-surface-border bg-surface-raised p-3">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Buscar por equipo..."
          className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Liga:</span>
          {/* Filtro de seleccion, nunca de deseleccion (regla fija del
              proyecto): empieza SIN ninguna marcada (= todas visibles) y
              solo una a la vez -- clic en la ya marcada la quita, clic en
              otra la sustituye. Nunca un boton "Todas" que parezca
              "seleccionado" por defecto. */}
          {competitions.map((c) => (
            <button
              key={c.code}
              onClick={() => setCompetitionCode((prev) => (prev === c.code ? null : c.code))}
              className={
                "rounded-full border px-3 py-1 " +
                (competitionCode === c.code
                  ? "border-slate-400 bg-slate-700 text-slate-100"
                  : "border-surface-border text-slate-400 hover:bg-surface/60")
              }
            >
              {c.name}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Estado:</span>
          {([
            ["scheduled", "Por jugar"],
            ["finished", "Jugados"],
            ["all", "Todos"],
          ] as [StatusFilter, string][]).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setStatus(value)}
              className={
                "rounded-full border px-3 py-1 " +
                (status === value
                  ? "border-slate-400 bg-slate-700 text-slate-100"
                  : "border-surface-border text-slate-400 hover:bg-surface/60")
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">{error}</div>
      )}

      {!error && loading && <div className="text-sm text-slate-500">Buscando...</div>}

      {!error && !loading && matches.length === 0 && (
        <div className="rounded border border-surface-border bg-surface-raised p-6 text-sm text-slate-400">
          Sin partidos para estos filtros.
        </div>
      )}

      {!error && !loading && matches.length > 0 && (
        <div className="flex flex-col gap-1">
          {matches.map((match) => (
            <Link
              key={match.id}
              href={`/matches/${match.id}`}
              className="flex items-center justify-between gap-3 rounded border border-surface-border bg-surface-raised px-3 py-2 text-sm hover:bg-black/20"
            >
              <div className="flex items-center gap-3">
                <span className="w-28 shrink-0 text-xs text-slate-500">{formatKickoff(match.kickoff_utc)}</span>
                <span className="text-slate-200">
                  {match.home_team.canonical_name}
                  {match.status === "finished" ? (
                    <span className="mx-1.5 font-semibold text-slate-100">
                      {match.home_goals} - {match.away_goals}
                    </span>
                  ) : (
                    <span className="mx-1.5 text-slate-500">vs</span>
                  )}
                  {match.away_team.canonical_name}
                </span>
              </div>
              <span className="text-xs text-slate-500">{match.competition.name}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
