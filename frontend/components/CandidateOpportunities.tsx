"use client";

import Link from "next/link";
import TeamLogo from "@/components/TeamLogo";
import { apiFetch } from "@/lib/api";
import { useCachedData } from "@/lib/clientCache";
import { MARKET_DISPLAY_NAMES } from "@/types";

type Row = {
  match: { id: number; kickoff_utc: string; competition: { code: string; name: string };
    home: { id: number; name: string; logo_url: string | null }; away: { id: number; name: string; logo_url: string | null } };
  opportunity: { market: string; probability: number; odds: number; bookmaker: string; ev: number;
    quote_age_minutes: number | null; probability_source: string; model_version_id: number | null };
  eligible: number; local_evidence: number;
};

const when = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Champions, AFC Elite and Liga F: verified opportunities of the on-the-fly candidate model, shown apart. */
export default function CandidateOpportunities({ days = 3 }: { days?: number }) {
  const { data, error } = useCachedData<Row[]>(`candidate-opportunities:${days}`, () => apiFetch<Row[]>(`/matches/candidate-opportunities?days=${days}`));
  return (
    <section className="mt-6 rounded-xl border border-violet-500/30 bg-card">
      <div className="border-b border-surface-border p-4">
        <h2 className="text-lg font-semibold text-slate-100">Champions, AFC Elite y Liga F</h2>
        <p className="mt-1 text-sm text-slate-400">
          Oportunidades verificadas de su modelo candidato, que se calcula al vuelo y todavía no ha pasado la promoción de modelos.
          Por eso van aparte del ranking principal. Aplican los mismos filtros (probabilidad ≥ 65 %, cuota 1,30–1,80 vigente y EV positivo),
          pero sin historial local liquidado. El bot sí las evalúa.
        </p>
      </div>
      {error && <p className="p-4 text-sm text-slate-400">No se pudieron consultar.</p>}
      {!data && !error && <p className="p-4 text-sm text-slate-400">Calculando previsiones (la primera vez tarda unos segundos)…</p>}
      {data && !data.length && <p className="p-4 text-sm text-slate-400">Ninguna oportunidad verificada en los próximos {days} días.</p>}
      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full whitespace-nowrap text-sm">
            <thead className="text-left text-xs text-slate-400"><tr>
              <th className="px-3 py-2">Partido</th><th className="px-3 py-2">Fecha</th><th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Prob. modelo</th><th className="px-3 py-2">Cuota</th><th className="px-3 py-2">EV estimado</th>
              <th className="px-3 py-2">Cuota actualizada</th>
            </tr></thead>
            <tbody>
              {data.map(({ match, opportunity: o }) => (
                <tr key={match.id} className="border-t border-surface-border hover:bg-surface-alt/40">
                  <td className="px-3 py-2">
                    <Link href={`/matches/${match.id}`} className="flex items-center gap-1.5 hover:underline">
                      <TeamLogo name={match.home.name} logoUrl={match.home.logo_url} size="sm" />{match.home.name}
                      <span className="text-slate-500">vs</span>
                      <TeamLogo name={match.away.name} logoUrl={match.away.logo_url} size="sm" />{match.away.name}
                    </Link>
                    <div className="text-xs text-slate-400">{match.competition.name}</div>
                  </td>
                  <td className="px-3 py-2 text-slate-400">{when.format(new Date(match.kickoff_utc))}</td>
                  <td className="px-3 py-2 text-slate-300">{MARKET_DISPLAY_NAMES[o.market] ?? o.market}</td>
                  <td className="px-3 py-2 font-semibold text-slate-100">{(o.probability * 100).toFixed(1)}%
                    {o.probability_source === "original" && <span className="text-xs font-normal text-slate-400"> (sin calibrar)</span>}</td>
                  <td className="px-3 py-2">{o.odds.toFixed(2)} <span className="text-xs text-slate-500">{o.bookmaker}</span></td>
                  <td className="px-3 py-2 text-emerald-400">+{(o.ev * 100).toFixed(1)}%</td>
                  <td className="px-3 py-2 text-slate-400">{o.quote_age_minutes != null ? `origen hace ${Math.round(o.quote_age_minutes)} min` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
