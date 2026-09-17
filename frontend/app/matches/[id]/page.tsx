import { notFound } from "next/navigation";
import { getMatch, getMatchPredictions } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";

export default async function MatchDetailPage({ params }: { params: { id: string } }) {
  const matchId = Number(params.id);
  if (Number.isNaN(matchId)) notFound();

  const [match, predictions] = await Promise.all([
    getMatch(matchId).catch(() => null),
    getMatchPredictions(matchId).catch(() => []),
  ]);

  if (!match) notFound();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">{match.competition.name}</div>
        <h1 className="text-2xl font-semibold text-slate-100">
          {match.home_team.canonical_name} <span className="text-slate-500">vs</span>{" "}
          {match.away_team.canonical_name}
        </h1>
        <div className="text-sm text-slate-500">
          {new Date(match.kickoff_utc).toLocaleString("es-ES")} · {match.status}
          {match.status === "finished" && ` · ${match.home_goals} - ${match.away_goals}`}
        </div>
      </div>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Prediction table
        </h2>
        <div className="overflow-hidden rounded-lg border border-surface-border">
          <table className="w-full text-sm">
            <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2">Market</th>
                <th className="px-3 py-2">Model</th>
                <th className="px-3 py-2">Market</th>
                <th className="px-3 py-2">Edge</th>
                <th className="px-3 py-2">Fair Odds</th>
                <th className="px-3 py-2">Signal</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((p) => (
                <tr key={p.id} className="border-t border-surface-border">
                  <td className="px-3 py-2 text-slate-200">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</td>
                  <td className="px-3 py-2">{(p.model_probability * 100).toFixed(1)}%</td>
                  <td className="px-3 py-2">
                    {p.market_probability !== null ? `${(p.market_probability * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td className="px-3 py-2">{p.edge !== null ? `${(p.edge * 100).toFixed(1)} pp` : "—"}</td>
                  <td className="px-3 py-2">{p.fair_odds.toFixed(2)}</td>
                  <td className="px-3 py-2">
                    <SignalBadge tier={p.signal_tier} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {predictions.length === 0 && (
            <div className="p-6 text-sm text-slate-500">Sin predicciones para este partido todavia.</div>
          )}
        </div>
      </section>

      {predictions.length > 0 && (
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Explanation — {MARKET_DISPLAY_NAMES[predictions[0].market]}
          </h2>
          <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
            <ol className="flex flex-col gap-2 text-sm text-slate-300">
              {predictions[0].explanation.map((factor, i) => (
                <li key={factor.feature} className="flex items-center justify-between">
                  <span>
                    {i + 1}. {factor.display_name}
                  </span>
                  <span
                    className={
                      factor.direction === "increases_probability" ? "text-edge-positive" : "text-edge-negative"
                    }
                  >
                    {factor.direction === "increases_probability" ? "▲" : "▼"}
                  </span>
                </li>
              ))}
              {predictions[0].explanation.length === 0 && <li>Sin factores calculados.</li>}
            </ol>
          </div>
        </section>
      )}
    </div>
  );
}
