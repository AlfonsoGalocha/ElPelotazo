"use client";

import Link from "next/link";
import { useState } from "react";
import TeamLogo from "@/components/TeamLogo";
import { useCachedData } from "@/lib/clientCache";
import { getTeamDirectory, type TeamDirectoryEntry } from "@/lib/analysis";
import { EmptyNote } from "./StatsKit";

export default function TeamsDirectory() {
  const { data, error } = useCachedData<TeamDirectoryEntry[]>("team-directory", getTeamDirectory);
  const [query, setQuery] = useState("");
  const [competition, setCompetition] = useState("");
  if (error) return <EmptyNote>No se pudo cargar el listado de equipos.</EmptyNote>;
  if (!data) return <div className="h-64 animate-pulse rounded-lg bg-surface-alt/40" aria-label="Cargando equipos" />;
  const competitions = [...new Map(data.flatMap(t => t.competitions.map(c => [c.code, c.name] as [string, string]))).entries()]
    .sort((a, b) => a[1].localeCompare(b[1]));
  const q = query.trim().toLowerCase();
  const teams = data.filter(t => (!q || t.name.toLowerCase().includes(q)) && (!competition || t.competitions.some(c => c.code === competition)));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input type="search" placeholder="Buscar equipo…" value={query} onChange={e => setQuery(e.target.value)} aria-label="Buscar equipo"
               className="min-w-0 flex-1 rounded border border-surface-border bg-surface-raised px-3 py-1.5 text-sm" />
        <select aria-label="Competición" value={competition} onChange={e => setCompetition(e.target.value)}
                className="rounded border border-surface-border bg-surface-raised px-2 py-1.5 text-sm">
          <option value="">Todas las competiciones</option>
          {competitions.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
      </div>
      <p className="text-xs text-slate-500">{teams.length} equipos con partidos en el último año.</p>
      <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {teams.slice(0, 300).map(t => (
          <li key={t.id}>
            <Link href={`/teams/${t.id}`} className="flex items-center gap-2 rounded-lg border border-surface-border bg-card px-3 py-2 hover:bg-surface-alt/50">
              <TeamLogo name={t.name} logoUrl={t.logo_url} size="sm" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{t.name}</span>
              <span className="truncate text-xs text-slate-500">{t.competitions[0]?.name}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
