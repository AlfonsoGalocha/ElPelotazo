import { notFound } from "next/navigation";
import { getMatch, getMatchPredictions } from "@/lib/api";
import type { Prediction } from "@/types";
import { CARDS_MARKETS, CORNERS_MARKETS, GOALS_MARKETS, MARKET_DISPLAY_NAMES, MATCH_RESULT_MARKETS } from "@/types";
import SignalBadge from "@/components/SignalBadge";

const SECTIONS: { label: string; markets: string[] }[] = [
  { label: "🏆 Resultado (1X2)", markets: MATCH_RESULT_MARKETS },
  { label: "⚽ Goles", markets: GOALS_MARKETS },
  { label: "🟨 Tarjetas", markets: CARDS_MARKETS },
  { label: "🚩 Córners", markets: CORNERS_MARKETS },
];

function PredictionTable({ predictions }: { predictions: Prediction[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-surface-border">
      <table className="w-full text-sm">
        <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">Mercado</th>
            <th className="px-3 py-2">Modelo</th>
            <th className="px-3 py-2">Mercado</th>
            <th className="px-3 py-2">Cuota</th>
            <th className="px-3 py-2">Casas</th>
            <th className="px-3 py-2">Edge</th>
            <th className="px-3 py-2">Cuota justa</th>
            <th className="px-3 py-2">Señal</th>
          </tr>
        </thead>
        <tbody>
          {predictions.map((p) => (
            <tr key={p.id} className="border-t border-surface-border">
              <td className="px-3 py-2 text-slate-200">
                {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
                {!p.has_market && <span className="ml-2 text-[10px] text-slate-500">sin mercado</span>}
              </td>
              <td className="px-3 py-2">{(p.model_probability * 100).toFixed(1)}%</td>
              <td className="px-3 py-2">
                {p.market_probability !== null ? `${(p.market_probability * 100).toFixed(1)}%` : "—"}
              </td>
              <td className="px-3 py-2">{p.market_odds !== null ? p.market_odds.toFixed(2) : "—"}</td>
              <td className="px-3 py-2 text-slate-400">{p.bookmakers_used ?? "—"}</td>
              <td className="px-3 py-2">{p.edge !== null ? `${(p.edge * 100).toFixed(1)} pp` : "—"}</td>
              <td className="px-3 py-2">{p.fair_odds.toFixed(2)}</td>
              <td className="px-3 py-2">
                <SignalBadge tier={p.signal_tier} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExplanationCard({ prediction }: { prediction: Prediction }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
        Explicación — {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market} (
        {(prediction.model_probability * 100).toFixed(1)}%)
      </div>
      <ol className="flex flex-col gap-2 text-sm text-slate-300">
        {prediction.explanation.map((factor, i) => (
          <li key={factor.feature} className="flex items-center justify-between">
            <span>
              {i + 1}. {factor.display_name}
            </span>
            <span
              className={factor.direction === "increases_probability" ? "text-edge-positive" : "text-edge-negative"}
            >
              {factor.direction === "increases_probability" ? "▲" : "▼"}
            </span>
          </li>
        ))}
        {prediction.explanation.length === 0 && <li>Sin factores calculados.</li>}
      </ol>
    </div>
  );
}

export default async function MatchDetailPage({ params }: { params: { id: string } }) {
  const matchId = Number(params.id);
  if (Number.isNaN(matchId)) notFound();

  const [match, predictions] = await Promise.all([
    getMatch(matchId).catch(() => null),
    getMatchPredictions(matchId).catch(() => []),
  ]);

  if (!match) notFound();

  const mainPrediction = predictions.find((p) => p.market === "over_2_5") ?? predictions[0];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">
          {match.competition.name}
          {match.matchday != null && ` · Jornada ${match.matchday}`}
        </div>
        <h1 className="text-2xl font-semibold text-slate-100">
          {match.home_team.canonical_name} <span className="text-slate-500">vs</span>{" "}
          {match.away_team.canonical_name}
        </h1>
        <div className="text-sm text-slate-500">
          {new Date(match.kickoff_utc).toLocaleString("es-ES")} · {match.status}
          {match.status === "finished" && ` · ${match.home_goals} - ${match.away_goals}`}
        </div>
      </div>

      {predictions.length === 0 && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-6 text-sm text-slate-500">
          Sin predicciones para este partido todavía.
        </div>
      )}

      {SECTIONS.map((section) => {
        const sectionPredictions = predictions.filter((p) => section.markets.includes(p.market));
        if (sectionPredictions.length === 0) return null;
        return (
          <section key={section.label}>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
              {section.label}
            </h2>
            <PredictionTable predictions={sectionPredictions} />
          </section>
        );
      })}

      {mainPrediction && (
        <section>
          <ExplanationCard prediction={mainPrediction} />
        </section>
      )}
    </div>
  );
}
