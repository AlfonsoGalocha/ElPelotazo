"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Globe } from "lucide-react";
import { getCompetitions, getMatchesList } from "@/lib/api";
import { formatMatchDateOnly, formatMatchTimeOnly } from "@/lib/dates";
import { INTERNATIONAL_COMPETITION_CODES } from "@/lib/internationalCompetitions";
import type { Competition, Match } from "@/types";
import TeamLogo from "@/components/TeamLogo";
import CompetitionBadge from "@/components/CompetitionBadge";
import { EmptyState, ErrorState } from "@/components/EmptyState";
import { MatchCardSkeletonGrid } from "@/components/Skeleton";

// Pagina dedicada "Selecciones" (pedido real del usuario, 2026-11): a
// diferencia de /matches (una unica liga de club siempre tiene partidos
// casi cada semana), los partidos de selecciones se concentran en pocos
// "parones" al año -- lo que el usuario pidio ver claramente es CUANDO
// seran los proximos, agrupados por fecha, no un listado plano. El
// desglose completo de cada partido (prediccion, comparativa
// modelo-vs-mercado) sigue viviendo en /matches/[id] -- esta pagina NUNCA
// duplica esa logica, solo enlaza a ella.
function groupByDate(matches: Match[]): [string, Match[]][] {
  const groups = new Map<string, Match[]>();
  for (const match of matches) {
    const key = formatMatchDateOnly(match.kickoff_utc, {
      weekday: "long",
      day: "2-digit",
      month: "long",
    });
    const existing = groups.get(key);
    if (existing) {
      existing.push(match);
    } else {
      groups.set(key, [match]);
    }
  }
  return Array.from(groups.entries());
}

function MatchRow({ match }: { match: Match }) {
  return (
    <Link
      href={`/matches/${match.id}`}
      className="flex items-center justify-between gap-3 rounded-lg border border-surface-border bg-card px-3 py-2.5 text-sm transition-colors hover:border-accent/30 hover:bg-surface-alt/40"
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className="w-14 shrink-0 text-xs text-slate-500">
          {formatMatchTimeOnly(match.kickoff_utc, { hour: "2-digit", minute: "2-digit" })}
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
  );
}

function MatchDateGroup({ title, matches }: { title: string; matches: Match[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="px-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      {matches.map((match) => (
        <MatchRow key={match.id} match={match} />
      ))}
    </div>
  );
}

export default function SeleccionesPage() {
  const [competitions, setCompetitions] = useState<Competition[]>([]);
  const [competitionCode, setCompetitionCode] = useState<string | null>(null);
  const [upcoming, setUpcoming] = useState<Match[]>([]);
  const [recent, setRecent] = useState<Match[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompetitions()
      .then((all) => setCompetitions(all.filter((c) => INTERNATIONAL_COMPETITION_CODES.includes(c.code))))
      .catch(() => setCompetitions([]));
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const codes = competitionCode ? [competitionCode] : INTERNATIONAL_COMPETITION_CODES;

    Promise.all(
      codes.map((code) =>
        Promise.all([
          getMatchesList({ competitionCode: code, status: "scheduled", limit: 100 }),
          getMatchesList({ competitionCode: code, status: "finished", limit: 10 }),
        ])
      )
    )
      .then((results) => {
        const allUpcoming = results.flatMap(([u]) => u);
        const allRecent = results.flatMap(([, r]) => r);
        allUpcoming.sort((a, b) => new Date(a.kickoff_utc).getTime() - new Date(b.kickoff_utc).getTime());
        allRecent.sort((a, b) => new Date(b.kickoff_utc).getTime() - new Date(a.kickoff_utc).getTime());
        setUpcoming(allUpcoming);
        setRecent(allRecent.slice(0, 10));
      })
      .catch(() => setError("No se pudo conectar con la API."))
      .finally(() => setLoading(false));
  }, [competitionCode]);

  const upcomingGroups = groupByDate(upcoming);
  const nextMatch = upcoming[0] ?? null;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
          <Globe className="h-6 w-6 text-accent" />
          Selecciones
        </h1>
        <p className="text-sm text-slate-500">
          Partidos de selecciones nacionales -- se concentran en pocos parones al año, aquí ves
          cuándo son los próximos de un vistazo. El desglose completo de cada partido está en su
          ficha, igual que con los clubes.
        </p>
      </div>

      {competitions.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-surface-border bg-card p-4 text-xs">
          <span className="text-slate-500">Competición:</span>
          {competitions.map((c) => (
            <button
              key={c.code}
              onClick={() => setCompetitionCode((prev) => (prev === c.code ? null : c.code))}
              className={
                "rounded-full border px-3 py-1 transition-colors " +
                (competitionCode === c.code
                  ? "border-accent/50 bg-accent/15 text-slate-100"
                  : "border-surface-border text-slate-400 hover:bg-surface-alt/60")
              }
            >
              {c.name}
            </button>
          ))}
        </div>
      )}

      {error && <ErrorState message={error} />}

      {!error && loading && <MatchCardSkeletonGrid count={3} />}

      {!error && !loading && upcoming.length === 0 && recent.length === 0 && (
        <EmptyState
          icon={Globe}
          title="Sin partidos de selecciones todavía"
          description="Ingiere el histórico/calendario con 'football-edge update-international' antes de ver nada aquí."
        />
      )}

      {!error && !loading && nextMatch && (
        <section>
          <div className="mb-2 px-1 text-sm font-semibold text-slate-200">Próximo partido</div>
          <MatchRow match={nextMatch} />
        </section>
      )}

      {!error && !loading && upcomingGroups.length > 0 && (
        <section className="flex flex-col gap-4">
          <div className="px-1 text-sm font-semibold text-slate-200">Próximos partidos</div>
          {upcomingGroups.map(([date, matches]) => (
            <MatchDateGroup key={date} title={date} matches={matches} />
          ))}
        </section>
      )}

      {!error && !loading && recent.length > 0 && (
        <section className="flex flex-col gap-1.5">
          <div className="px-1 text-sm font-semibold text-slate-200">Últimos resultados</div>
          {recent.map((match) => (
            <MatchRow key={match.id} match={match} />
          ))}
        </section>
      )}
    </div>
  );
}
