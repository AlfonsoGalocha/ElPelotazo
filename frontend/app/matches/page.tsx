"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";
import { getCompetitions, getMatchesList } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
import type { Competition, Match } from "@/types";
import TeamLogo from "@/components/TeamLogo";
import CompetitionBadge from "@/components/CompetitionBadge";
import { EmptyState, ErrorState } from "@/components/EmptyState";

type StatusFilter = "all" | "scheduled" | "finished";

function formatKickoff(iso: string): string {
  return formatMatchDateTime(iso, {
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
  // Pedido real del usuario: "ponme el mismo filtro de oportunidades en
  // todas las secciones de la app" -- mismo desplegable con optgroup
  // Ligas/Selecciones que /top-signals, nunca botones sueltos.
  const clubCompetitions = competitions.filter((c) => !isInternationalCompetition(c.code));
  const internationalCompetitions = competitions.filter((c) => isInternationalCompetition(c.code));

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
        <h1 className="mb-1 text-2xl font-bold tracking-tight text-slate-100">Partidos</h1>
        <p className="text-sm text-slate-500">
          Busca cualquier partido de las 5 ligas, próximo o ya jugado.
        </p>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-surface-border bg-card p-4">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por equipo..."
            className="w-full rounded-lg border border-surface-border bg-surface py-2 pl-9 pr-3 text-sm text-slate-200 placeholder:text-slate-500 focus:border-accent/50 focus:outline-none"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Liga:</span>
          <select
            value={competitionCode ?? ""}
            onChange={(e) => setCompetitionCode(e.target.value || null)}
            className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
          >
            <option value="">Todas</option>
            <optgroup label="Ligas">
              {clubCompetitions.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name}
                </option>
              ))}
            </optgroup>
            {internationalCompetitions.length > 0 && (
              <optgroup label="Selecciones">
                {internationalCompetitions.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
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
                "rounded-full border px-3 py-1 transition-colors " +
                (status === value
                  ? "border-accent/50 bg-accent/15 text-slate-100"
                  : "border-surface-border text-slate-400 hover:bg-surface-alt/60")
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <ErrorState message={error} />}

      {!error && loading && (
        <div className="flex flex-col gap-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg border border-surface-border bg-card" />
          ))}
        </div>
      )}

      {!error && !loading && matches.length === 0 && (
        <EmptyState title="Sin partidos para estos filtros" description="Prueba a quitar algún filtro o buscar otro equipo." />
      )}

      {!error && !loading && matches.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {matches.map((match) => (
            <Link
              key={match.id}
              href={`/matches/${match.id}`}
              className="flex items-center justify-between gap-3 rounded-lg border border-surface-border bg-card px-3 py-2.5 text-sm transition-colors hover:border-accent/30 hover:bg-surface-alt/40"
            >
              <div className="flex min-w-0 items-center gap-3">
                <span className="w-28 shrink-0 text-xs text-slate-500">
                  {formatKickoff(match.kickoff_utc)}
                  {match.round_name && <span className="block">{match.round_name}{match.status === "live" ? " · En juego" : ""}</span>}
                </span>
                <div className="flex min-w-0 items-center gap-2 text-slate-200">
                  <TeamLogo name={match.home_team.canonical_name} logoUrl={match.home_team.logo_url} size="sm" />
                  <span className="truncate">{match.home_team.canonical_name}</span>
                  {match.status === "finished" ? (
                    <span className="mx-1 shrink-0 font-semibold text-slate-100">
                      {match.home_goals} - {match.away_goals}
                    </span>
                  ) : (
                    <span className="mx-1 shrink-0 text-slate-500">vs</span>
                  )}
                  <span className="truncate">{match.away_team.canonical_name}</span>
                  <TeamLogo name={match.away_team.canonical_name} logoUrl={match.away_team.logo_url} size="sm" />
                </div>
              </div>
              <CompetitionBadge
                name={match.competition.name}
                country={match.competition.country}
                className="shrink-0 text-xs text-slate-500"
              />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
