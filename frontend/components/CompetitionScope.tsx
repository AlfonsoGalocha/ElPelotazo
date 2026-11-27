"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { Competition, Match } from "@/types";

export type CompetitionScope = "primary" | "experimental" | "all";
const preferenceKey = "elpelotazo.competitionScope";

export function useCompetitionScope() {
  const [scope, setScope] = useState<CompetitionScope>("primary");
  useEffect(() => {
    const saved = localStorage.getItem(preferenceKey);
    if (saved === "primary" || saved === "experimental" || saved === "all") setScope(saved);
  }, []);
  return [scope, (value: CompetitionScope) => {
    setScope(value);
    localStorage.setItem(preferenceKey, value);
  }] as const;
}

export function inCompetitionScope(competition: Competition, scope: CompetitionScope) {
  const group = competition.competition_scope;
  return group !== "other" && (scope === "all" || (group ?? "primary") === scope);
}

export function CompetitionScopeSelect({ value, onChange }: {
  value: CompetitionScope;
  onChange: (value: CompetitionScope) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-slate-400">
      Ámbito
      <select value={value} onChange={event => onChange(event.target.value as CompetitionScope)}
        className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200">
        <option value="primary">Principales</option>
        <option value="experimental">Experimentales</option>
        <option value="all">Todas</option>
      </select>
    </label>
  );
}

export function CompetitionMatchGroups({ matches, children }: {
  matches: Match[];
  children: (group: Match[]) => ReactNode;
}) {
  const groups = new Map<string, Match[]>();
  for (const match of matches) {
    const group = groups.get(match.competition.code) ?? [];
    group.push(match);
    groups.set(match.competition.code, group);
  }
  const sorted = [...groups.values()].sort((a, b) =>
    Number(a[0].competition.competition_scope === "experimental") -
    Number(b[0].competition.competition_scope === "experimental"));
  return sorted.map((group, index) => {
    const competition = group[0].competition;
    return (
      <details key={competition.code} open={index === 0} className="rounded-lg border border-surface-border p-2">
        <summary className="cursor-pointer px-2 py-1 text-sm">
          {competition.name} · {group.length} {group.length === 1 ? "partido" : "partidos"}
          {competition.competition_scope === "experimental" && <span className="ml-2 text-xs text-amber-300">Experimental</span>}
        </summary>
        {children(group)}
      </details>
    );
  });
}
