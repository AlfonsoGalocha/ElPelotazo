import Link from "next/link";
import { AlertTriangle, TrendingUp } from "lucide-react";
import { getCompetitions, getOpportunityDiagnostics, getTopSignals } from "@/lib/api";
import type { SignalScope } from "@/lib/api";
import { formatMatchDateOnly } from "@/lib/dates";
import { isInternationalCompetition } from "@/lib/internationalCompetitions";
import { MARKET_DISPLAY_NAMES } from "@/types";
import MarketQualityBadge from "@/components/MarketQualityBadge";
import AnomalyBadges from "@/components/AnomalyBadge";
import { EmptyState } from "@/components/EmptyState";

const EXCLUSION_LABELS: Record<string, string> = {
  sin_mercado: "Sin cuota para ese mercado",
  cuota_fuera_de_banda: "Fuera del rango 1,35–1,90 de la estrategia",
  cuota_desactualizada: "Cuota desactualizada",
  origen_cuota_no_verificado: "Hora de la cuota sin verificar",
  calibracion_no_disponible: "Probabilidad sin calibrar",
  calibracion_en_extremo: "Calibración en 0 % o 100 % pendiente de validar",
  ajuste_calibracion_sin_respaldo: "Ajuste de calibración superior a 15 puntos sin evidencia de la liga y mercado",
  incertidumbre_elevada_o_no_disponible: "Intervalo amplio o no disponible",
  cuota_sin_frescura_alta: "Cuota fuera del límite de alta confianza",
  incertidumbre_no_disponible: "Incertidumbre sin estimar",
  ev_estimado_no_positivo: "Sin ventaja estimada a esa cuota",
  ev_conservador_no_positivo: "Sin ventaja con la estimación conservadora",
  fiabilidad_historica_insuficiente: "Evidencia histórica insuficiente",
};

const SCOPE_LABELS: Record<SignalScope, string> = {
  current_round: "Jornada actual",
  next_round: "Próxima jornada",
  all_upcoming: "Todas las próximas",
};

// Mision "oportunidades" seccion 2.2 (fiabilidad jerarquica): nunca
// mostrar un nivel "market_tier"/"market_global" como si fuera especifico
// de esta competicion exacta -- la etiqueta deja claro de donde sale.
const RELIABILITY_SOURCE_LABELS: Record<string, string> = {
  competition_market: "esta liga",
  // Bloque 2.2 de la mision (shrinkage bayesiano, ver
  // evaluation_service.py::TRACK_RECORD_SHRINKAGE_K): mezcla de poca
  // muestra de esta liga con un respaldo fiable -- nunca se presenta
  // como igual de especifica que "esta liga" puro.
  competition_market_shrunk: "esta liga (muestra pequeña, ajustada)",
  market_tier: "ligas similares",
  market_global: "global",
};

export default async function TopSignalsPage({
  searchParams,
}: {
  searchParams: {
    date?: string;
    limit?: string;
    per_match?: string;
    prices?: string;
    view?: string;
    scope?: SignalScope;
    competition_code?: string;
    days?: string;
    min_fair_odds?: string;
    max_fair_odds?: string;
    min_edge?: string;
    min_bookmakers?: string;
    min_ev?: string;
    quality?: "HIGH" | "MEDIUM" | "LOW" | "ALL";
  };
}) {
  const { date, competition_code: competitionCode } = searchParams;
  const limit = searchParams.limit === "5" ? 5 : 10;
  const view = searchParams.view === "explore" ? "explore" : searchParams.view === "high" ? "high" : "main";
  const valueOnly = view !== "explore";
  const allPrices = searchParams.prices !== "preferred";
  const perMatch = searchParams.per_match === "2" ? 2 : 1;
  // `date` tiene prioridad sobre `scope` (ver backend); mostrarlo como su
  // propio modo evita que el selector de jornada contradiga al de fecha.
  const scope: SignalScope = date ? "all_upcoming" : (searchParams.scope ?? "current_round");
  const days = searchParams.days ? Number(searchParams.days) : 4;
  const minFairOdds = searchParams.min_fair_odds ? Number(searchParams.min_fair_odds) : undefined;
  const maxFairOdds = searchParams.max_fair_odds ? Number(searchParams.max_fair_odds) : undefined;
  const minEdge = searchParams.min_edge ? Number(searchParams.min_edge) : undefined;
  const minBookmakers = searchParams.min_bookmakers ? Number(searchParams.min_bookmakers) : undefined;
  const minEv = searchParams.min_ev ? Number(searchParams.min_ev) / 100 : undefined;
  const quality = searchParams.quality ?? "ALL";
  const hasFairOddsFilter = minFairOdds !== undefined || maxFairOdds !== undefined;
  const hasAnyFilter =
    view !== "main" || !allPrices || Boolean(date) ||
    hasFairOddsFilter ||
    Boolean(competitionCode) ||
    scope !== "current_round" ||
    minEdge !== undefined ||
    minBookmakers !== undefined ||
    minEv !== undefined ||
    quality !== "ALL";
  // Pedido explicito del usuario ("quitame tanto filtro"): solo Jornada +
  // Competicion quedan siempre visibles (lo que se usa a diario). El resto
  // sigue existiendo tal cual (nada se borra de la API ni se pierde al
  // recargar), solo detras de "Filtros avanzados" -- abierto por defecto
  // si ya habia alguno de esos puesto, para no esconder un filtro activo.
  const hasAdvancedFilter =
    Boolean(date) ||
    hasFairOddsFilter ||
    (searchParams.days !== undefined && searchParams.days !== "4") ||
    minEdge !== undefined ||
    minBookmakers !== undefined ||
    minEv !== undefined ||
    quality !== "ALL";

  const [rows, competitions, diagnostics] = await Promise.all([
    getTopSignals({
      limit,
      perMatch,
      allPrices,
      opportunityView: view,
      date,
      scope,
      days,
      competitionCode,
      minFairOdds,
      maxFairOdds,
      minEdge,
      minBookmakers,
      minEv,
      quality,
    }, true).catch(() => null),
    getCompetitions().catch(() => []),
    getOpportunityDiagnostics({
      date, scope, days, competition_code: competitionCode,
      min_fair_odds: minFairOdds, max_fair_odds: maxFairOdds,
      min_edge: minEdge, min_bookmakers: minBookmakers, min_ev: minEv, quality,
    }).catch(() => null),
  ]);
  // Pedido real del usuario: "lo importante son las ligas" -- las ligas
  // de club quedan como opciones principales del selector, las
  // competiciones de selecciones (15 a la vez, seccion 18 de
  // architecture_audit.md) en su propio grupo separado dentro del MISMO
  // desplegable (nunca mezcladas en una sola lista plana).
  const predictions = rows ?? [];
  const firstReasons = diagnostics?.first_reasons;
  const staleInBand = firstReasons?.cuota_desactualizada ?? 0;
  const unavailableCalibration = firstReasons?.calibracion_no_disponible ?? 0;
  const negativeEv = firstReasons?.ev_estimado_no_positivo ?? 0;
  const inBand = diagnostics?.counts.candidates_in_band ?? 0;
  const blockers = [
    staleInBand > 0 && `${staleInBand} con cuota antigua`,
    unavailableCalibration > 0 && `${unavailableCalibration} sin calibración`,
    negativeEv > 0 && `${negativeEv} con EV no positivo`,
  ].filter(Boolean);
  const emptyExplanation = view === "main" && inBand > 0 && blockers.length
    ? `De ${inBand} selecciones en el rango de la estrategia, estos fueron los principales descartes: ${blockers.join(", ")}.`
    : undefined;
  const clubCompetitions = competitions.filter((c) => !isInternationalCompetition(c.code));
  const internationalCompetitions = competitions.filter((c) => isInternationalCompetition(c.code));

  return (
    <div>
      <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
        <TrendingUp className="h-5 w-5 text-accent" />
        Mejores oportunidades
      </h1>
      <p className="mb-4 text-sm text-slate-500">
        {date
          ? `Solo partidos del ${formatMatchDateOnly(`${date}T00:00:00`, {
              weekday: "long",
              day: "2-digit",
              month: "short",
            })}.`
          : scope === "all_upcoming"
            ? `De los próximos ${days} días (sin restricción de jornada).`
            : `${SCOPE_LABELS[scope]} de cada competición — nunca un partido de otra jornada aunque tenga más edge.`}{" "}
        {view === "explore" ? "Exploración de selecciones con cuota." : "La estrategia exige una cuota verificable, probabilidad calibrada y valor esperado positivo."}{" "}
        {view === "high" && "Alta confianza exige además historial suficiente, pronta actualización y valor conservador positivo."}{" "}
        {hasFairOddsFilter && (
          <>
            Filtrado además a cuota justa del modelo entre {minFairOdds ?? "0"} y {maxFairOdds ?? "∞"}.
          </>
        )}
      </p>

      <nav aria-label="Categorías de oportunidades" className="mb-4 flex flex-wrap gap-4 text-sm">
        {([["main", "Mejores oportunidades"], ["high", "Alta confianza"], ["explore", "Candidatos y descartes"]] as const).map(([key, label]) => (
          <Link key={key} aria-current={view === key ? "page" : undefined}
            className={view === key ? "font-semibold text-accent" : "text-slate-400 hover:text-slate-100"}
            href={{ pathname: "/top-signals", query: { ...searchParams, view: key } }}>{label}</Link>
        ))}
      </nav>

      <form className="mb-6 flex flex-wrap items-center gap-2 text-sm text-slate-400">
        <label htmlFor="scope">Jornada:</label>
        <select
          id="scope"
          name="scope"
          defaultValue={scope}
          className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        >
          <option value="current_round">Jornada actual</option>
          <option value="next_round">Próxima jornada</option>
          <option value="all_upcoming">Todas las próximas</option>
        </select>
        <label htmlFor="competition_code" className="ml-2">
          Competición:
        </label>
        <select
          id="competition_code"
          name="competition_code"
          defaultValue={competitionCode ?? ""}
          className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
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
        <input type="hidden" name="view" value={view} />
        {view === "explore" && <><label htmlFor="prices">Cuotas</label>
        <select id="prices" name="prices" defaultValue={allPrices ? "all" : "preferred"} className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200">
          <option value="preferred">1,35–1,90</option>
          <option value="all">Todos los rangos</option>
        </select></>}
        <button type="submit" className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-1 font-medium text-slate-100 transition-colors hover:bg-accent/20">
          Aplicar
        </button>
        {hasAnyFilter && (
          <a href="/top-signals" className="text-slate-500 underline hover:text-slate-300">
            Quitar filtros
          </a>
        )}

        <details className="mt-1 w-full" open={hasAdvancedFilter}>
          <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">
            Filtros avanzados
          </summary>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label htmlFor="days">(días si &quot;todas las próximas&quot;):</label>
            <input
              id="days"
              type="number"
              name="days"
              min="1"
              max="30"
              defaultValue={searchParams.days ?? "4"}
              className="w-16 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <label htmlFor="date" className="ml-2">
              O fecha concreta:
            </label>
            <input
              id="date"
              type="date"
              name="date"
              defaultValue={date ?? ""}
              className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <label htmlFor="limit">Selecciones</label>
            <select id="limit" name="limit" defaultValue={String(limit)}
              className="rounded border border-surface-border bg-surface-raised px-2 py-1">
              <option value="5">Top 5</option><option value="10">Top 10</option>
            </select>
            <label htmlFor="per_match">Máximo por partido</label>
            <select id="per_match" name="per_match" defaultValue={String(perMatch)}
              className="rounded border border-surface-border bg-surface-raised px-2 py-1">
              <option value="1">Una</option><option value="2">Dos de mercados distintos</option>
            </select>
            <label htmlFor="min_fair_odds" className="ml-2">
              Cuota justa entre:
            </label>
            <input
              id="min_fair_odds"
              type="number"
              name="min_fair_odds"
              step="0.01"
              min="1"
              placeholder="1.00"
              defaultValue={searchParams.min_fair_odds ?? ""}
              className="w-20 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <span>y</span>
            <input
              id="max_fair_odds"
              type="number"
              name="max_fair_odds"
              step="0.01"
              min="1"
              placeholder="2.00"
              defaultValue={searchParams.max_fair_odds ?? ""}
              className="w-20 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <label htmlFor="min_edge" className="ml-2">
              Edge mínimo (pp):
            </label>
            <input
              id="min_edge"
              type="number"
              name="min_edge"
              step="0.5"
              min="0"
              placeholder="0"
              defaultValue={searchParams.min_edge ?? ""}
              className="w-16 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <label htmlFor="min_ev" className="ml-2">
              EV mínimo (%):
            </label>
            <input
              id="min_ev"
              type="number"
              name="min_ev"
              step="1"
              placeholder="0"
              defaultValue={searchParams.min_ev ?? ""}
              className="w-16 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <label htmlFor="min_bookmakers" className="ml-2">
              Casas mín.:
            </label>
            <input
              id="min_bookmakers"
              type="number"
              name="min_bookmakers"
              min="1"
              placeholder="1"
              defaultValue={searchParams.min_bookmakers ?? ""}
              className="w-16 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            />
            <label htmlFor="quality" className="ml-2">
              Calidad de mercado:
            </label>
            <select
              id="quality"
              name="quality"
              defaultValue={quality}
              className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
            >
              <option value="ALL">Todas</option>
              <option value="HIGH">Alta</option>
              <option value="MEDIUM">Media</option>
              <option value="LOW">Baja</option>
            </select>
            <button
              type="submit"
              className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-1 font-medium text-slate-100 transition-colors hover:bg-accent/20"
            >
              Aplicar
            </button>
          </div>
        </details>
      </form>
      {predictions.length > 0 && !predictions.some(p => p.has_statistical_value) && (
        <p className="mb-4 rounded-lg border border-surface-border p-3 text-sm text-slate-300">
          No se han identificado oportunidades con valor demostrado para esta jornada. Puedes consultar abajo las selecciones con cuota y el motivo por el que se descartaron.
        </p>
      )}
      {diagnostics && (
        <details className="mb-4 rounded-lg border border-surface-border p-3 text-sm text-slate-300">
          <summary className="cursor-pointer font-medium">Diagnóstico de oportunidades</summary>
          <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Partidos analizados", diagnostics.counts.matches],
              ["Selecciones analizadas", diagnostics.counts.candidates],
              ["Con cuota real", diagnostics.counts.candidates_with_valid_market],
              [`Dentro de ${diagnostics.min_odds}–${diagnostics.max_odds}`, diagnostics.counts.candidates_in_band],
              ["Con probabilidad utilizable", diagnostics.counts.usable_probabilities],
              ["Con EV positivo", diagnostics.counts.positive_estimated_ev],
              ["Superan los requisitos", diagnostics.counts.passed_gate],
              ["Alta confianza antes de agrupar por partido", diagnostics.counts.high_confidence],
              ["En el ranking de 10 partidos", diagnostics.counts.opportunities],
            ].map(([label, value]) => (
              <div key={label}><dt className="text-slate-400">{label}</dt><dd>{value}</dd></div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-slate-400">Motivos simultáneos. Una selección puede contar varias veces.</p>
          <details className="mt-2 text-xs text-slate-400">
            <summary>Primer fallo por selección</summary>
            {Object.entries(diagnostics.first_reasons).map(([reason, count]) => (
              <p key={reason}>{EXCLUSION_LABELS[reason] ?? reason.replaceAll("_", " ")} · {count}</p>
            ))}
          </details>
          {Object.keys(diagnostics.reasons).length > 0 && (
            <ul className="mt-3 space-y-1 border-t border-surface-border pt-2">
              {Object.entries(diagnostics.reasons).sort((a, b) => b[1] - a[1]).map(([reason, count]) => (
                <li key={reason}>{EXCLUSION_LABELS[reason] ?? reason.replaceAll("_", " ")} · {count}</li>
              ))}
            </ul>
          )}
        </details>
      )}

      <div className="overflow-hidden rounded-xl border border-surface-border bg-card">
        <div className="overflow-x-auto"><table className="w-full text-sm">
          <thead className="bg-surface-alt/60 text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Partido</th>
              <th className="px-3 py-2">Fecha</th>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Prob. modelo</th>
              <th className="px-3 py-2">Prob. mercado</th>
              <th className="px-3 py-2">Cuota</th>
              <th className="px-3 py-2">Casas</th>
              <th className="px-3 py-2">EV estimado</th>
              <th className="px-3 py-2">EV conservador</th>
              <th className="px-3 py-2 text-slate-600">Edge (info)</th>
              <th className="px-3 py-2">Calidad de datos</th>
              <th className="px-3 py-2">Calidad de mercado</th>
              <th className="px-3 py-2">Fiabilidad histórica</th>
              <th className="px-3 py-2">Cuota actualizada</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p) => {
              const assessment = p.opportunity;
              const price = assessment?.quote_price;
              const ev = assessment?.ev;
              const conservative = assessment?.conservative_ev;
              const edge = assessment?.edge;
              const age = assessment?.quote_age_minutes;
              return <tr key={p.id} className="border-t border-surface-border hover:bg-surface-alt/40">
                <td className="px-3 py-2 text-slate-200">
                  <Link href={`/signals/${p.id}`} className="hover:underline">
                    {p.match.home_team.canonical_name} vs {p.match.away_team.canonical_name}
                  </Link>
                  <div className="text-xs text-slate-400">{p.match.competition.name}</div>
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {formatMatchDateOnly(p.match.kickoff_utc, {
                    weekday: "short",
                    day: "2-digit",
                    month: "short",
                  })}
                </td>
                <td className="px-3 py-2 text-slate-300">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</td>
                <td className="px-3 py-2 font-semibold text-slate-100">
                  {p.calibrated_probability !== null
                    ? `${(p.calibrated_probability * 100).toFixed(1)}%`
                    : `${(p.model_probability * 100).toFixed(1)}% (sin calibrar)`}
                </td>
                <td className="px-3 py-2 text-slate-300">
                  {p.market_probability !== null
                    ? `${(p.market_probability * 100).toFixed(1)}% ${p.market_probability_source?.endsWith("_no_vig") ? "(sin margen)" : "(estimada)"}`
                    : "—"}
                </td>
                <td className="px-3 py-2">
                  {price != null ? (
                    <>
                      {price.toFixed(2)}
                      <div className="text-xs text-slate-400">{assessment?.bookmaker ?? "Referencia"}</div>
                      <div className="text-xs text-slate-400">{(100 / price).toFixed(1)}% equilibrio</div>
                    </>
                  ) : "—"}
                </td>
                <td className="px-3 py-2 text-slate-400">{p.bookmakers_used ?? "—"}</td>
                <td className={ev != null && ev > 0 && p.has_statistical_value ? "px-3 py-2 text-emerald-400" : "px-3 py-2 text-slate-300"}>
                  {ev != null ? `${ev >= 0 ? "+" : ""}${(ev * 100).toFixed(1)}%` : "—"}
                </td>
                <td className={conservative != null && conservative > 0 && p.has_statistical_value ? "px-3 py-2 text-emerald-400" : "px-3 py-2 text-slate-300"}>
                  {conservative != null ? `${conservative >= 0 ? "+" : ""}${(conservative * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="px-3 py-2 text-slate-600">
                  {edge != null ? `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)} pp` : "—"}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap items-center gap-1">
                    <span className={p.has_statistical_value ? "font-semibold text-emerald-400" : "text-slate-400"}>
                      {p.has_statistical_value ? (assessment?.high_confidence ? "Alta confianza" : "Valor estimado · confianza limitada") : "Descartada"}
                    </span>
                    <span className="text-xs text-slate-400">
                      {(p.has_statistical_value ? assessment?.confidence_reasons : assessment?.reasons)?.map(
                        (reason) => EXCLUSION_LABELS[reason] ?? reason.replaceAll("_", " ")
                      ).join(" · ")}
                    </span>
                    {p.has_statistical_value && <span className="text-xs text-slate-400">EV positivo con cuota verificable</span>}
                    <AnomalyBadges flags={p.anomaly_flags} />
                  </div>
                </td>
                <td className="px-3 py-2">
                  <MarketQualityBadge quality={p.market_quality} />
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {p.reliability ? (
                    <span
                      className="cursor-help"
                      title={`${p.reliability.n_observations} liquidaciones reales usadas (${RELIABILITY_SOURCE_LABELS[p.reliability.source_level]})`}
                    >
                      {(p.reliability.value * 100).toFixed(0)}%{" "}
                      <span className="text-[10px] text-slate-500">
                        ({RELIABILITY_SOURCE_LABELS[p.reliability.source_level]})
                      </span>
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-3 py-2 text-slate-400">
                  <span className="flex items-center gap-1">
                    {p.is_stale_odds && <AlertTriangle className="h-3 w-3 shrink-0 text-amber-400" />}
                    {age != null ? `hace ${Math.round(age)} min` : "Origen sin verificar"}
                  </span>
                </td>
              </tr>;
            })}
          </tbody>
        </table></div>
        {predictions.length === 0 && (
          <div className="p-2">
            <EmptyState description={rows ? emptyExplanation : undefined} title={!rows ? "No se pudieron consultar las selecciones" : valueOnly ? (view === "high" ? "Ninguna selección reúne la evidencia adicional de alta confianza" : "Ninguna selección combina cuota verificable y valor esperado positivo") : "No hay selecciones con cuota válida para los criterios actuales"} />
            {rows && valueOnly && (
              <Link href={{ pathname: "/top-signals", query: { ...searchParams, view: "explore" } }} className="block pb-4 text-center text-sm text-accent underline">
                Explorar selecciones con cuota
              </Link>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
