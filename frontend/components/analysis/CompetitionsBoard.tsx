"use client";

import Link from "next/link";
import { useState } from "react";
import TeamLogo from "@/components/TeamLogo";
import { getCompetitions } from "@/lib/api";
import { useCachedData } from "@/lib/clientCache";
import { getCompetitionOverview, type CompetitionFixture, type CompetitionOverview } from "@/lib/analysis";
import type { Competition } from "@/types";
import { EmptyNote, Panel } from "./StatsKit";

const when = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

function Fixtures({ rows, result }: { rows: CompetitionFixture[]; result?: boolean }) {
  if (!rows.length) return <EmptyNote>Sin partidos.</EmptyNote>;
  return (
    <ul className="divide-y divide-surface-border text-sm">
      {rows.map(m => (
        <li key={m.id}>
          <Link href={`/matches/${m.id}`} className="grid grid-cols-[3.5rem_1fr_auto_1fr] items-center gap-2 py-1.5 hover:bg-surface-alt/40">
            <span className="text-xs text-slate-500">{when.format(new Date(m.kickoff))}</span>
            <span className="truncate text-right">{m.home.name}</span>
            <span className="font-semibold tabular-nums">{result ? `${m.home_goals ?? "–"}–${m.away_goals ?? "–"}` : "vs"}</span>
            <span className="truncate">{m.away.name}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default function CompetitionsBoard() {
  const competitions = useCachedData<Competition[]>("competitions", getCompetitions).data ?? [];
  const [code, setCode] = useState("laliga");
  const { data, error } = useCachedData<CompetitionOverview>(`competition:${code}`, () => getCompetitionOverview(code));
  const current = data?.coverage.at(-1);
  return (
    <div className="space-y-3">
      <div className="-mx-1 flex gap-1 overflow-x-auto px-1">
        {competitions.map(c => (
          <button key={c.code} type="button" onClick={() => setCode(c.code)} aria-pressed={c.code === code}
                  className={`shrink-0 rounded-full border px-3 py-1 text-xs ${c.code === code ? "border-violet-400 bg-violet-500/15 text-slate-100" : "border-surface-border text-slate-400 hover:text-slate-200"}`}>
            {c.name}
          </button>
        ))}
      </div>
      {error && <EmptyNote>No se pudo cargar la competición.</EmptyNote>}
      {!data && !error && <div className="h-64 animate-pulse rounded-lg bg-surface-alt/40" aria-label="Cargando" />}
      {data && (
        <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]">
          <Panel title="Clasificación" hint={data.standings[0] ? `Archivada el ${when.format(new Date(data.standings[0].as_of))}` : undefined}>
            {data.standings.length ? (
              <div className="overflow-x-auto">
                <table className="w-full whitespace-nowrap text-sm">
                  <thead className="text-xs text-slate-400"><tr>
                    {["#", "Equipo", "PJ", "G", "E", "P", "GF", "GC", "DG", "Pts"].map(h => <th key={h} scope="col" className={`px-1.5 py-1 ${h === "Equipo" ? "text-left" : "text-right"}`}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {data.standings.map(row => (
                      <tr key={row.team.id} className="border-t border-surface-border">
                        <td className="px-1.5 py-1 text-right text-slate-400">{row.position}</td>
                        <td className="px-1.5 py-1"><Link href={`/teams/${row.team.id}`} className="flex items-center gap-1.5 hover:underline">
                          <TeamLogo name={row.team.name} logoUrl={row.team.logo_url} size="sm" />{row.team.name}</Link></td>
                        {[row.played, row.won, row.drawn, row.lost, row.goals_for, row.goals_against, row.goal_difference].map((v, i) =>
                          <td key={i} className="px-1.5 py-1 text-right tabular-nums">{v}</td>)}
                        <td className="px-1.5 py-1 text-right font-semibold tabular-nums">{row.points}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyNote>Sin clasificación archivada para esta competición.</EmptyNote>}
          </Panel>
          <div className="space-y-3">
            <Panel title="Próximos partidos"><Fixtures rows={data.upcoming} /></Panel>
            <Panel title="Últimos resultados"><Fixtures rows={data.recent} result /></Panel>
            <Panel title="Cobertura estadística" hint={current ? `Temporada ${current.season}: ${current.corners}/${current.finished} con córners, ${current.shots}/${current.finished} con tiros, ${current.shots_on_target}/${current.finished} con tiros a puerta.` : undefined}>
              <div className="max-h-56 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="text-slate-400"><tr>{["Temporada", "Jugados", "Córners", "Tiros", "A puerta", "Descanso"].map(h =>
                    <th key={h} scope="col" className={`px-1.5 py-1 ${h === "Temporada" ? "text-left" : "text-right"}`}>{h}</th>)}</tr></thead>
                  <tbody>
                    {[...data.coverage].reverse().map(row => (
                      <tr key={row.season} className="border-t border-surface-border">
                        <td className="px-1.5 py-1">{row.season}</td>
                        {[row.finished, row.corners, row.shots, row.shots_on_target, row.halftime].map((v, i) =>
                          <td key={i} className={`px-1.5 py-1 text-right tabular-nums ${i > 0 && v < row.finished ? "text-amber-300" : ""}`}>{v}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}
