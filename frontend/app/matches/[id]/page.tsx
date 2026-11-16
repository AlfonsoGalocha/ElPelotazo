import { notFound } from "next/navigation";
import Link from "next/link";
import { Bot, Trophy, CircleDot, Flag, ChevronDown } from "lucide-react";
import { getMatch, getMatchForm, getMatchPredictions } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";
import type { Prediction } from "@/types";
import { CORNERS_MARKETS, GOALS_MARKETS, MARKET_DISPLAY_NAMES, MATCH_RESULT_MARKETS } from "@/types";
import SignalBadge from "@/components/SignalBadge";
import MatchTopPicks from "@/components/MatchTopPicks";
import MatchFormSection from "@/components/MatchFormSection";
import ModelVsMarketChart from "@/components/ModelVsMarketChart";
import RisksSection from "@/components/RisksSection";
import JarvisChat from "@/components/JarvisChat";
import TeamLogo from "@/components/TeamLogo";
import CompetitionBadge from "@/components/CompetitionBadge";

const SECTIONS: { label: string; icon: typeof Trophy; markets: string[] }[] = [
  { label: "Resultado (1X2)", icon: Trophy, markets: MATCH_RESULT_MARKETS },
  { label: "Goles", icon: CircleDot, markets: GOALS_MARKETS },
  { label: "Córners", icon: Flag, markets: CORNERS_MARKETS },
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
                {p.has_market ? (
                  <Link href={`/signals/${p.id}`} className="hover:underline">
                    {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
                  </Link>
                ) : (
                  <>
                    {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
                    <span className="ml-2 text-[10px] text-slate-500">sin mercado</span>
                  </>
                )}
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

  const [match, predictions, form] = await Promise.all([
    getMatch(matchId).catch(() => null),
    getMatchPredictions(matchId).catch(() => []),
    getMatchForm(matchId).catch(() => null),
  ]);

  if (!match) notFound();

  const mainPrediction = predictions.find((p) => p.is_best_prediction) ?? predictions[0];

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-xl border border-surface-border bg-card p-5">
        <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-wide text-slate-500">
          <CompetitionBadge name={match.competition.name} country={match.competition.country} />
          {match.matchday != null && <span>· Jornada {match.matchday}</span>}
        </div>
        <div className="flex flex-wrap items-center justify-center gap-4 sm:justify-between">
          <div className="flex items-center gap-3">
            <TeamLogo name={match.home_team.canonical_name} size="lg" />
            <span className="text-lg font-bold text-slate-100">{match.home_team.canonical_name}</span>
          </div>
          <div className="flex flex-col items-center gap-1">
            {match.status === "finished" ? (
              <span className="text-2xl font-extrabold text-slate-100">
                {match.home_goals} - {match.away_goals}
              </span>
            ) : (
              <span className="text-sm font-semibold text-slate-500">VS</span>
            )}
            <span className="text-xs text-slate-500">
              {formatMatchDateTime(match.kickoff_utc)} · {match.status === "finished" ? "Finalizado" : "Programado"}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-lg font-bold text-slate-100">{match.away_team.canonical_name}</span>
            <TeamLogo name={match.away_team.canonical_name} size="lg" />
          </div>
        </div>
      </div>

      <MatchTopPicks predictions={predictions} />

      {mainPrediction && (
        <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Modelo vs mercado — {MARKET_DISPLAY_NAMES[mainPrediction.market] ?? mainPrediction.market}
          </h2>
          <ModelVsMarketChart prediction={mainPrediction} />
        </section>
      )}

      {predictions.length > 0 && <RisksSection predictions={predictions} />}

      {form && <MatchFormSection form={form} />}

      <details className="rounded-lg border border-surface-border">
        <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm text-slate-400 hover:text-slate-200">
          <Bot className="h-4 w-4" />
          Pregúntale a la IA sobre este partido
        </summary>
        <div className="border-t border-surface-border p-4">
          <JarvisChat
            matchContext={{
              matchId: match.id,
              homeTeam: match.home_team.canonical_name,
              awayTeam: match.away_team.canonical_name,
            }}
          />
        </div>
      </details>

      {predictions.length > 0 && (
        <details className="rounded-lg border border-surface-border">
          <summary className="flex cursor-pointer items-center gap-2 px-4 py-3 text-sm text-slate-400 hover:text-slate-200">
            <ChevronDown className="h-4 w-4" />
            Ver todos los mercados y la explicación completa
          </summary>
          <div className="flex flex-col gap-6 border-t border-surface-border p-4">
            {SECTIONS.map((section) => {
              const sectionPredictions = predictions.filter((p) => section.markets.includes(p.market));
              if (sectionPredictions.length === 0) return null;
              const Icon = section.icon;
              return (
                <section key={section.label}>
                  <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-slate-400">
                    <Icon className="h-3.5 w-3.5" />
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
        </details>
      )}
    </div>
  );
}
