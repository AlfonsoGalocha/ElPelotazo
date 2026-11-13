"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getHeadToHead, getTeams } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import type { Team, TeamHeadToHead } from "@/types";
import ResultDots from "./ResultDots";

function TeamPicker({
  label,
  teams,
  selected,
  onSelect,
  exclude,
}: {
  label: string;
  teams: Team[];
  selected: Team | null;
  onSelect: (team: Team | null) => void;
  exclude: number | undefined;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const results =
    query.trim().length === 0
      ? []
      : teams
          .filter((t) => t.id !== exclude && t.canonical_name.toLowerCase().includes(query.trim().toLowerCase()))
          .slice(0, 8);

  if (selected) {
    return (
      <div className="flex items-center justify-between gap-2 rounded border border-surface-border bg-surface px-3 py-2 text-sm">
        <span className="text-slate-100">{selected.canonical_name}</span>
        <button
          onClick={() => {
            onSelect(null);
            setQuery("");
          }}
          className="text-xs text-slate-500 hover:text-slate-300"
        >
          Cambiar
        </button>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        placeholder={label}
        className="w-full rounded border border-surface-border bg-surface px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none"
      />
      {open && results.length > 0 && (
        <div className="absolute z-10 mt-1 w-full rounded-lg border border-surface-border bg-surface-raised shadow-lg">
          {results.map((t) => (
            <button
              key={t.id}
              onClick={() => {
                onSelect(t);
                setQuery("");
                setOpen(false);
              }}
              className="block w-full border-b border-surface-border px-3 py-2 text-left text-sm text-slate-200 last:border-0 hover:bg-black/20"
            >
              {t.canonical_name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Pedido real del usuario (pagina principal): elegir dos equipos
// cualquiera y ver su cara a cara + que es mas probable que pase entre
// ellos. "Mas probable" SOLO se muestra si hay un partido REAL ya
// programado entre ambos con predicciones ya generadas (ver backend) --
// nunca se inventa un enfrentamiento hipotetico.
export default function HeadToHeadWidget() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamA, setTeamA] = useState<Team | null>(null);
  const [teamB, setTeamB] = useState<Team | null>(null);
  const [data, setData] = useState<TeamHeadToHead | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTeams()
      .then(setTeams)
      .catch(() => setTeams([]));
  }, []);

  useEffect(() => {
    if (!teamA || !teamB) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    getHeadToHead(teamA.id, teamB.id)
      .then(setData)
      .catch(() => setError("No se pudo consultar el cara a cara."))
      .finally(() => setLoading(false));
  }, [teamA, teamB]);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-300">Cara a cara (H2H)</div>
      <p className="mb-3 text-xs text-slate-500">
        Elige dos equipos para ver su historial directo. Si hay un partido real ya programado entre
        ellos, también se muestra lo más probable según el modelo.
      </p>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <TeamPicker label="Equipo A" teams={teams} selected={teamA} onSelect={setTeamA} exclude={teamB?.id} />
        <TeamPicker label="Equipo B" teams={teams} selected={teamB} onSelect={setTeamB} exclude={teamA?.id} />
      </div>

      {error && <div className="mt-3 text-sm text-red-300">{error}</div>}
      {loading && <div className="mt-3 text-sm text-slate-500">Buscando...</div>}

      {data && !loading && (
        <div className="mt-4 flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="mb-1 text-xs text-slate-500">{data.team_a.team.canonical_name} — últimos partidos</div>
              <ResultDots matches={data.team_a.recent} />
            </div>
            <div>
              <div className="mb-1 text-xs text-slate-500">{data.team_b.team.canonical_name} — últimos partidos</div>
              <ResultDots matches={data.team_b.recent} />
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs text-slate-500">
              Enfrentamientos directos ({data.head_to_head.length})
            </div>
            {data.head_to_head.length === 0 ? (
              <span className="text-xs text-slate-500">Sin enfrentamientos previos registrados.</span>
            ) : (
              <div className="flex flex-col gap-1">
                {data.head_to_head.map((m) => (
                  <div key={m.match_id} className="flex items-center gap-2 text-xs text-slate-400">
                    <span className="w-24 shrink-0">
                      {new Date(m.kickoff_utc).toLocaleDateString("es-ES", {
                        day: "2-digit",
                        month: "short",
                        year: "numeric",
                      })}
                    </span>
                    <span>
                      {m.venue === "home" ? data.team_a.team.canonical_name : data.team_b.team.canonical_name}{" "}
                      <span className="text-slate-200">
                        {m.goals_for}-{m.goals_against}
                      </span>{" "}
                      {m.opponent}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-surface-border pt-3">
            {!data.scheduled_match && (
              <span className="text-xs text-slate-500">
                No hay ningún partido programado entre ellos actualmente.
              </span>
            )}
            {data.scheduled_match && !data.scheduled_match.best_prediction && (
              <span className="text-xs text-slate-500">
                Tienen un partido programado el{" "}
                {new Date(data.scheduled_match.kickoff_utc).toLocaleDateString("es-ES", {
                  day: "2-digit",
                  month: "short",
                })}
                , pero todavía no hay predicciones generadas para él.
              </span>
            )}
            {data.scheduled_match?.best_prediction && (
              <Link
                href={`/matches/${data.scheduled_match.match_id}`}
                className="flex flex-col gap-1 rounded border border-opportunity/30 bg-opportunity/5 px-3 py-2 hover:bg-opportunity/10"
              >
                <div className="text-xs text-opportunity">
                  Lo más probable en su próximo partido (
                  {new Date(data.scheduled_match.kickoff_utc).toLocaleDateString("es-ES", {
                    day: "2-digit",
                    month: "short",
                  })}
                  )
                </div>
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span className="text-slate-100">
                    {MARKET_DISPLAY_NAMES[data.scheduled_match.best_prediction.market] ??
                      data.scheduled_match.best_prediction.market}
                  </span>
                  <span className="flex items-center gap-2">
                    {data.scheduled_match.best_prediction.market_odds != null && (
                      <span className="text-xs text-slate-400">
                        cuota {data.scheduled_match.best_prediction.market_odds.toFixed(2)}
                      </span>
                    )}
                    <span className="font-semibold text-slate-100">
                      {(data.scheduled_match.best_prediction.model_probability * 100).toFixed(1)}%
                    </span>
                  </span>
                </div>
              </Link>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
