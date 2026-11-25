import { notFound } from "next/navigation";
import Link from "next/link";
import { Bot, Trophy, CircleDot, Flag, ChevronDown } from "lucide-react";
import { getMatch, getMatchPredictions, getMatchPreview, getPreMatchForecast, type PreMatchForecast } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";
import type { Prediction } from "@/types";
import { CORNERS_MARKETS, GOALS_MARKETS, MARKET_DISPLAY_NAMES, MATCH_RESULT_MARKETS } from "@/types";
import SignalBadge from "@/components/SignalBadge";
import MatchTopPicks from "@/components/MatchTopPicks";
import MatchAnalysis from "@/components/analysis/MatchAnalysis";
import MatchHeaderContext from "@/components/analysis/MatchHeaderContext";
import PredictionExplain from "@/components/analysis/PredictionExplain";
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
          {predictions.map((p) => {
            const probability = p.calibrated_probability ?? p.model_probability;
            const gap = p.market_probability === null ? null : probability - p.market_probability;
            return <tr key={p.id} className="border-t border-surface-border">
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
              <td className="px-3 py-2">{(probability * 100).toFixed(1)}% {p.calibrated_probability === null && <span className="text-xs text-slate-400">(sin calibrar)</span>}</td>
              <td className="px-3 py-2">
                {p.market_probability !== null ? `${(p.market_probability * 100).toFixed(1)}%` : "—"}
              </td>
              <td className="px-3 py-2">{p.market_odds !== null ? p.market_odds.toFixed(2) : "—"}</td>
              <td className="px-3 py-2 text-slate-400">{p.bookmakers_used ?? "—"}</td>
              <td className="px-3 py-2">{gap !== null ? `${(gap * 100).toFixed(1)} pp` : "—"}</td>
              <td className="px-3 py-2">{probability > 0 ? (1 / probability).toFixed(2) : "—"}</td>
              <td className="px-3 py-2">
                <SignalBadge tier={p.signal_tier} />
              </td>
            </tr>;
          })}
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

function Outcome({ value }: { value: boolean | null }) {
  if (value === null) return <span className="text-slate-400">Sin resolver</span>;
  return value ? <span className="text-emerald-400">✓ Acertó</span> : <span className="text-red-400">✗ Falló</span>;
}

function PreMatchSection({ forecast }: { forecast: PreMatchForecast }) {
  const picks = [["Predicción interesante", forecast.forecast], ["Oportunidad verificada", forecast.opportunity]] as const;
  return (
    <section className="space-y-3 rounded-lg border border-surface-border bg-card p-4">
      <h2 className="font-semibold">Predicción antes del partido</h2>
      <p className="text-sm text-slate-400">
        {forecast.source === "previsión_prepartido" ? "Última previsión mostrada antes del inicio" : "Captura prepartido del Laboratorio"}
        {" · "}{formatMatchDateTime(forecast.captured_at)}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {picks.map(([title, pick]) => (
          <div key={title} className="rounded border border-surface-border p-3 text-sm">
            <div className="text-xs text-slate-400">{title}</div>
            {pick ? (
              <>
                <div className="font-semibold">{MARKET_DISPLAY_NAMES[pick.market] ?? pick.market} · {(pick.probability * 100).toFixed(1)} %
                  {pick.odds != null && <> · {pick.odds.toFixed(2)}</>}</div>
                <Outcome value={pick.is_correct} />
              </>
            ) : <div className="text-slate-400">Sin selección</div>}
          </div>
        ))}
      </div>
      {forecast.markets.length > 0 && (
        <details>
          <summary className="cursor-pointer text-sm text-slate-400">Todos los mercados ({forecast.markets.length})</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full whitespace-nowrap text-left text-sm">
              <thead><tr>{["Mercado", "Probabilidad", "Cuota", "Resultado"].map(label =>
                <th scope="col" key={label} className="p-2 text-slate-400">{label}</th>)}</tr></thead>
              <tbody>{forecast.markets.map(row => (
                <tr key={row.market} className="border-t border-surface-border">
                  <td className="p-2">{MARKET_DISPLAY_NAMES[row.market] ?? row.market}</td>
                  <td className="p-2">{(row.probability * 100).toFixed(1)} %</td>
                  <td className="p-2">{row.odds != null ? row.odds.toFixed(2) : "—"}</td>
                  <td className="p-2"><Outcome value={row.is_correct} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </details>
      )}
    </section>
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

  const preview = ["champions_league", "liga_f", "afc_champions_league"].includes(match.competition.code) && match.status === "scheduled" && predictions.length === 0
    ? await getMatchPreview(matchId).catch(() => null)
    : null;

  const preMatch = match.status === "finished" ? await getPreMatchForecast(matchId).catch(() => null) : null;

  const mainPrediction = predictions.find((p) => p.is_best_prediction) ?? predictions[0];

  const status = ({ finished: "Finalizado", live: "En juego", postponed: "Aplazado", cancelled: "Cancelado" } as Record<string, string>)[match.status] ?? "Programado";
  const predictionTab = (
    <div className="flex flex-col gap-6">
      {preMatch && <PreMatchSection forecast={preMatch} />}

      {!preview && !preMatch && ["champions_league", "liga_f", "afc_champions_league"].includes(match.competition.code) && predictions.length === 0 && (
        <p className="rounded-lg border border-surface-border p-3 text-sm text-slate-400">Pronóstico no disponible para este partido.
          {match.status === "scheduled" && match.has_pre_match_odds != null && (
            <span className="block">{match.has_pre_match_odds ? "Hay cuotas prepartido guardadas." : "Sin cuotas prepartido guardadas."}</span>
          )}
        </p>
      )}
      {preview && (
        <section className="space-y-3 rounded-lg border border-surface-border bg-card p-4">
          <h2 className="font-semibold">Previsión de {match.competition.name}</h2>
          <p className="text-sm text-slate-400">
            Candidato #{preview.model_version_id} · Actualizado {formatMatchDateTime(preview.evaluated_at)}.
            Las cuotas son de consenso y pueden variar antes del inicio.
            Es un modelo candidato: queda fuera del ranking principal y aparece aparte en «Mejores predicciones». El bot sí la evalúa.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full whitespace-nowrap text-left text-sm">
              <thead><tr>{["Mercado", "Modelo", "Prob. de mercado", "Cuota guardada", "Origen de cuotas"].map(label =>
                <th scope="col" key={label} className="p-2 text-slate-400">{label}</th>)}</tr></thead>
              <tbody>{preview.markets.map(row => (
                <tr key={row.market} className="border-t border-surface-border">
                  <td className="p-2">{MARKET_DISPLAY_NAMES[row.market] ?? row.market}</td>
                  <td className="p-2">{((row.calibrated_probability ?? row.original_probability) * 100).toFixed(1)} %
                    {row.calibrated_probability === null && <span className="text-xs text-slate-400"> · Original</span>}
                  </td>
                  <td className="p-2">{row.market_probability === null ? "—" : `${(row.market_probability * 100).toFixed(1)} %`}</td>
                  <td className="p-2">{row.odds === null ? "Sin cuota" : row.odds.toFixed(2)}</td>
                  <td className="p-2">{row.odds_origin_at && row.odds !== null ? formatMatchDateTime(row.odds_origin_at) : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}
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
      {predictions.length > 0 && <PredictionExplain matchId={match.id} predictions={predictions} />}
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      <header className="rounded-xl border border-surface-border bg-card px-4 py-3 sm:px-5 sm:py-4">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs uppercase tracking-wide text-slate-500">
          <CompetitionBadge name={match.competition.name} country={match.competition.country} />
          {match.round_name ? <span>· {match.round_name}</span> : match.matchday != null && <span>· Jornada {match.matchday}</span>}
          <span className="ml-auto normal-case tracking-normal">{formatMatchDateTime(match.kickoff_utc)}</span>
        </div>
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          <div className="flex min-w-0 flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-3">
            <TeamLogo name={match.home_team.canonical_name} logoUrl={match.home_team.logo_url} size="lg" />
            <div className="min-w-0">
              <Link href={`/teams/${match.home_team.id}`} className="block truncate text-base font-bold text-slate-100 hover:underline sm:text-lg">{match.home_team.canonical_name}</Link>
              <MatchHeaderContext matchId={match.id} side="home" />
            </div>
          </div>
          <div className="flex flex-col items-center">
            {match.status === "finished" || match.status === "live" ? (
              <span className="text-2xl font-extrabold tabular-nums text-slate-100">{match.home_goals ?? "–"} - {match.away_goals ?? "–"}</span>
            ) : <span className="text-sm font-semibold text-slate-500">VS</span>}
            <span className={`mt-0.5 rounded px-1.5 py-0.5 text-[11px] ${match.status === "live" ? "bg-emerald-500/15 text-emerald-300" : "bg-slate-500/10 text-slate-400"}`}>{status}</span>
          </div>
          <div className="flex min-w-0 flex-col items-end gap-1 text-right sm:flex-row-reverse sm:items-center sm:gap-3">
            <TeamLogo name={match.away_team.canonical_name} logoUrl={match.away_team.logo_url} size="lg" />
            <div className="min-w-0">
              <Link href={`/teams/${match.away_team.id}`} className="block truncate text-base font-bold text-slate-100 hover:underline sm:text-lg">{match.away_team.canonical_name}</Link>
              <MatchHeaderContext matchId={match.id} side="away" />
            </div>
          </div>
        </div>
      </header>
      <MatchAnalysis matchId={match.id} competitionName={match.competition.name} predictions={predictionTab} />
    </div>
  );
}
