"use client";

import Link from "next/link";
import { useState } from "react";
import { useCachedData } from "@/lib/clientCache";
import { Sparkles, SlidersHorizontal } from "lucide-react";
import { getTodayPreviews, getCompetitions, getMatchesList, getSummary, getTopSignals, type TodayPreview } from "@/lib/api";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
import { MARKET_DISPLAY_NAMES } from "@/types";
import type { Competition, Match, Prediction, Summary } from "@/types";
import MatchCard from "./MatchCard";
import { MatchCardSkeletonGrid } from "./Skeleton";
import { EmptyState, ErrorState } from "./EmptyState";
import StatsFooter from "./StatsFooter";
import TeamLogo from "./TeamLogo";
import { formatMatchDateOnly, formatMatchTimeOnly } from "@/lib/dates";

const madridDate = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Madrid", dateStyle: "short" });

const REASONS: Record<string, string> = {
  sin_modelo_valido: "Sin modelo válido",
  sin_prediccion_disponible: "Sin pronóstico generado",
  error_tecnico: "Error técnico",
  sin_mercado: "Mercado sin cuota",
  cuota_fuera_de_banda: "Cuota fuera de rango",
  cuota_desactualizada: "Cuota desactualizada",
  origen_cuota_no_verificado: "Origen de cuota sin verificar",
  ev_estimado_no_positivo: "EV estimado no positivo",
  ventaja_extrema_frente_al_mercado: "Discrepancia extrema con el mercado",
  probabilidad_inferior_al_65: "Probabilidad inferior al 65 %",
  fiabilidad_historica_insuficiente: "Evidencia histórica local insuficiente",
};
const label = (market: string) => MARKET_DISPLAY_NAMES[market] ?? market;
const percent = (value: number) => `${(value * 100).toFixed(1)} %`;

function PreviewDetails({ preview }: { preview: TodayPreview }) {
  const { forecast, opportunity, counts, markets, sensitivity } = preview;
  const countsLabels: Record<string, string> = {
    evaluated: "Evaluados", with_price: "Con cuota", minimum_probability: "Probabilidad ≥65 %",
    price_in_band: "Cuota en rango", current_price_in_band: "Cuota vigente en rango",
    positive_ev: "EV positivo a cuota observada", positive_ev_current_price: "EV positivo a cuota vigente en rango", local_evidence: "Evidencia local", eligible: "Aptos",
  };
  const scenarios: Record<string, string> = {
    current: "Política actual", minimum_60: "Mínimo 60 %", wider_band: "Cuota 1,25–1,90", extended_band: "Cuota 1,30–1,90",
    current_verified_quotes: "Solo cuotas con origen en los últimos 60 min", informative: "Informativa · 60 % y cuota 1,30–1,90",
  };
  return (
    <div className="px-3 pb-3 text-xs">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded border border-surface-border p-2">
          <div className="font-semibold text-slate-300">Mejor predicción interesante</div>
          {forecast ? (
            <div className="mt-1 text-slate-200">
              {label(forecast.market)} · {percent(forecast.probability)}
              <div>{forecast.odds.toFixed(2)} · {forecast.bookmaker} · {{current:"Cuota vigente", stale:"Cuota desactualizada · referencia", unverified:"Vigencia sin verificar · referencia"}[forecast.quote_status]}</div>
              <div>{forecast.ev === null ? "EV sin verificar" : `EV estimado ${forecast.ev >= 0 ? "+" : ""}${percent(forecast.ev)}`}</div>
              {!!forecast.strict_reasons.length && <div>{forecast.strict_reasons.map(r => REASONS[r] ?? r.replace(/_/g, " ")).join(" · ")}</div>}
              <div className="text-slate-400">
                {forecast.probability_source === "calibrated" ? "Calibrada" : "Original · sin calibración validada"}
                {forecast.limited_evidence ? " · Evidencia limitada" : ""}
              </div>
            </div>
          ) : <p className="mt-1 text-slate-400">Sin predicción interesante disponible{REASONS[preview.forecast_unavailable_reason ?? ""] ? ` · ${REASONS[preview.forecast_unavailable_reason ?? ""]}` : ""}</p>}
        </div>
        <div className="rounded border border-surface-border p-2">
          <div className="font-semibold text-slate-300">Oportunidad verificada</div>
          {opportunity ? (
            <div className="mt-1 text-slate-200">
              {label(opportunity.market)} · {percent(opportunity.probability)} · {opportunity.odds.toFixed(2)} · {opportunity.bookmaker}
              <div className="text-slate-400">Cuota vigente · EV estimado +{percent(opportunity.ev)} · Origen hace {Math.floor(opportunity.quote_age_minutes)} min
                {opportunity.probability_source === "original" ? " · Probabilidad original" : ""}
              </div>
            </div>
          ) : <p className="mt-1 text-slate-400">Sin oportunidad verificada</p>}
        </div>
      </div>
      <details className="mt-2 text-slate-400">
        <summary className="cursor-pointer">Diagnóstico y sensibilidad</summary>
        {forecast && (
          <p className="my-2">Modelo #{forecast.model_version_id} · <time dateTime={forecast.generated_at}>{new Date(forecast.generated_at).toLocaleString("es-ES")}</time>
            {forecast.observations != null && ` · ${forecast.observations} observaciones`}
            {forecast.interval && ` · Intervalo ${forecast.interval.map(percent).join("–")}`}
          </p>
        )}
        <p className="my-2">{Object.entries(countsLabels).map(([key, title]) => `${title} ${counts[key]}`).join(" · ")}</p>
        <p>Un mercado puede tener varios motivos. La evidencia local afecta a la confianza.</p>
        <ul className="my-2 space-y-1">
          {Object.entries(markets).map(([market, details]) => (
            <li key={market}>{label(market)} · {details.reasons.length ? details.reasons.map(reason => REASONS[reason] ?? reason.replace(/_/g, " ")).join(" · ") : "Apto"}
              {details.confidence_reasons.includes("fiabilidad_historica_insuficiente") ? " · Evidencia histórica local insuficiente" : ""}
              {details.quote_fetched_at && <span className="block text-slate-500">
                Origen {details.quote_origin_at ? new Date(details.quote_origin_at).toLocaleString("es-ES") : "sin fecha verificable"} · Consulta {new Date(details.quote_fetched_at).toLocaleString("es-ES")}
              </span>}
            </li>
          ))}
        </ul>
        <p className="pt-1 font-medium">Sensibilidad · sin efecto en el Laboratorio</p>
        <ul>{Object.entries(scenarios).map(([key, title]) => <li key={key}>{title} · {sensitivity[key] ? label(sensitivity[key]!.market) : "Sin selección"}</li>)}</ul>
      </details>
    </div>
  );
}

function ScheduleRows({ matches, picks, failed }: { matches: Match[]; picks?: Record<string, TodayPreview> | null; failed?: boolean }) {
  return (
    <ul className="divide-y divide-surface-border rounded-lg border border-surface-border bg-card">
      {matches.map((match) => {
        const pick = picks?.[match.id];
        return (
        <li key={match.id}>
          <Link href={`/matches/${match.id}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm hover:bg-surface-alt/60">
            <span className="flex flex-wrap items-center gap-2">
              <time className="text-xs text-slate-400" dateTime={match.kickoff_utc}>{formatMatchTimeOnly(match.kickoff_utc, { hour: "2-digit", minute: "2-digit" })}</time>
              <TeamLogo name={match.home_team.canonical_name} logoUrl={match.home_team.logo_url} size="sm" />
              {match.home_team.canonical_name}
              <span className="text-slate-400">{match.status === "finished" ? `${match.home_goals ?? "—"} – ${match.away_goals ?? "—"}` : "vs"}</span>
              {match.away_team.canonical_name}
              <TeamLogo name={match.away_team.canonical_name} logoUrl={match.away_team.logo_url} size="sm" />
            </span>
            <span className="text-xs text-slate-400">{match.competition.name}</span>
          </Link>
          {match.status === "scheduled" && picks !== undefined && (
            pick ? <PreviewDetails preview={pick} /> : (
              <p className="px-3 pb-3 text-xs text-slate-400">
                {failed ? "No se pudo consultar la previsión." : picks === null ? "Consultando previsión…" : "Predicción no disponible"}
              </p>
            )
          )}
        </li>
      ); })}
    </ul>
  );
}

type QualityFilter = "ALL" | "HIGH" | "MEDIUM" | "LOW";

const QUALITY_LABELS: Record<QualityFilter, string> = {
  ALL: "Todas",
  HIGH: "Alta confianza",
  MEDIUM: "Media confianza",
  LOW: "Baja confianza",
};

export default function DashboardBoard() {
  const [competitionCode, setCompetitionCode] = useState<string | null>(null);
  const [quality, setQuality] = useState<QualityFilter>("ALL");
  const competitions = useCachedData<Competition[]>("competitions", getCompetitions).data ?? [];
  const summary = useCachedData<Summary>("summary", getSummary).data ?? null;
  // The calendar API accepts UTC dates. Include the preceding UTC date around Madrid midnight.
  const scheduleState = useCachedData<Match[]>(`schedule:${competitionCode ?? "all"}`, () => {
    const now = Date.now();
    const dateFrom = new Date(now - 86_400_000).toISOString().slice(0, 10);
    const dateTo = new Date(now + 14 * 86_400_000).toISOString().slice(0, 10);
    return getMatchesList({ dateFrom, dateTo, competitionCode: competitionCode ?? undefined, limit: 500 });
  });
  const schedule = scheduleState.data ?? null;
  const scheduleError = scheduleState.error;
  const signals = useCachedData<Prediction[]>(`top-signals:${competitionCode ?? "all"}:${quality}`, () => getTopSignals({
    limit: 24,
    opportunityView: "explore",
    scope: "all_upcoming",
    days: 1,
    competitionCode: competitionCode ?? undefined,
    quality,
  }, true), { delayMs: 150 });
  const predictions = signals.data ?? [];
  const loading = signals.data === undefined && !signals.error;
  const error = signals.error ? "No se pudieron cargar las selecciones de hoy." : null;

  const today = madridDate.format(new Date());
  const todayPicks = predictions.filter(p => madridDate.format(new Date(p.match.kickoff_utc)) === today);
  const featured = todayPicks.filter(p => p.has_statistical_value).slice(0, 3);
  const remaining = todayPicks.filter(p => !featured.some(item => item.id === p.id));
  const todayFixtures = (schedule ?? []).filter(match => madridDate.format(new Date(match.kickoff_utc)) === today);
  const future = (schedule ?? []).filter(match =>
    match.status === "scheduled" && madridDate.format(new Date(match.kickoff_utc)) > today);
  const nextDates = [...new Set(future.map(match => madridDate.format(new Date(match.kickoff_utc))))].slice(0, 2);
  const visiblePreviewIds = [...todayFixtures, ...future.filter(match => nextDates.includes(madridDate.format(new Date(match.kickoff_utc))))]
    .filter(match => match.status === "scheduled").map(match => match.id).join(",");
  const previewState = useCachedData<Record<string, TodayPreview>>(visiblePreviewIds ? `previews:${visiblePreviewIds}` : null, () => {
    const ids = visiblePreviewIds.split(",").map(Number);
    return Promise.all(Array.from({ length: Math.ceil(ids.length / 200) }, (_, index) =>
      getTodayPreviews(ids.slice(index * 200, (index + 1) * 200)))).then(items => Object.assign({}, ...items));
  });
  const previews = previewState.data ?? null;
  const previewError = previewState.error;
  // Pedido real del usuario: "ponme el mismo filtro de oportunidades en
  // todas las secciones de la app" -- mismo desplegable con optgroup
  // Ligas/Selecciones que /top-signals, nunca botones sueltos.
  const clubCompetitions = competitions.filter((c) => !isInternationalCompetition(c.code));
  const internationalCompetitions = competitions.filter((c) => isInternationalCompetition(c.code));

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-wrap items-center gap-2 rounded-xl border border-surface-border bg-card p-3">
        <SlidersHorizontal className="h-4 w-4 shrink-0 text-slate-500" />
        <span className="text-xs text-slate-500">Liga:</span>
        <select
          value={competitionCode ?? ""}
          onChange={(e) => setCompetitionCode(e.target.value || null)}
          className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-xs text-slate-200"
        >
          <option value="">Todas</option>
          <optgroup label="Ligas">
            {clubCompetitions.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </optgroup>
          {internationalCompetitions.length > 0 && (
            <optgroup label="Selecciones">
              {internationalCompetitions.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <span className="ml-2 text-xs text-slate-500">Confianza:</span>
        {(Object.keys(QUALITY_LABELS) as QualityFilter[]).map((key) => (
          <button
            key={key}
            onClick={() => setQuality(key)}
            className={
              "rounded-full border px-3 py-1 text-xs transition-colors " +
              (quality === key
                ? "border-accent/50 bg-accent/15 text-slate-100"
                : "border-surface-border text-slate-400 hover:bg-surface-alt/60")
            }
          >
            {QUALITY_LABELS[key]}
          </button>
        ))}
      </section>

      {!loading && !error && featured.length > 0 && (
        <section>
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-200">
            <Sparkles className="h-4 w-4 text-opportunity" />
            Oportunidades con valor de hoy
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {featured.map((p) => (
              <MatchCard key={`featured-${p.id}`} prediction={p} />
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-200">Partidos de hoy</h2>
        {error && <ErrorState message={error} />}
        {!error && loading && <MatchCardSkeletonGrid count={3} />}
        {!loading && !error && remaining.length > 0 && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {remaining.map(p => <MatchCard key={p.id} prediction={p} />)}
          </div>
        )}
        {!loading && todayFixtures.length > 0 && <ScheduleRows matches={todayFixtures} picks={previews} failed={previewError} />}
        {!loading && schedule && !todayFixtures.length && !todayPicks.length && (
          <EmptyState title="No hay partidos hoy para esta competición." />
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-200">Próximos partidos</h2>
        {scheduleError && <ErrorState message="No se pudo cargar el calendario." />}
        {!schedule && !scheduleError && <p className="text-sm text-slate-400">Cargando calendario…</p>}
        {schedule && !nextDates.length && <EmptyState title="No hay partidos programados en las próximas dos semanas." />}
        {nextDates.map(day => {
          const matches = future.filter(match => madridDate.format(new Date(match.kickoff_utc)) === day);
          const champions = matches.filter(match => match.competition.code === "champions_league");
          const others = matches.filter(match => match.competition.code !== "champions_league");
          return (
            <div className="space-y-2" key={day}>
              {champions.length > 0 && (
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold">UEFA Champions League</h3>
                  <Link href="/opportunity-performance/strategies?competition=champions_league" className="text-xs text-accent">Ver en el Laboratorio</Link>
                </div>
              )}
              <p className="text-xs font-medium text-slate-400">{formatMatchDateOnly(matches[0].kickoff_utc, { weekday: "long", day: "numeric", month: "long" })}</p>
              {champions.length > 0 && <ScheduleRows matches={champions} picks={previews} failed={previewError} />}
              {others.length > 0 && <ScheduleRows matches={others} picks={previews} failed={previewError} />}
            </div>
          );
        })}
      </section>

      <StatsFooter summary={summary} />
    </div>
  );
}
