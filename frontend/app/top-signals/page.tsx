import Link from "next/link";
import { getCompetitions, getTopSignals } from "@/lib/api";
import type { SignalScope } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";
import MarketQualityBadge from "@/components/MarketQualityBadge";

const SCOPE_LABELS: Record<SignalScope, string> = {
  current_round: "Jornada actual",
  next_round: "Próxima jornada",
  all_upcoming: "Todas las próximas",
};

export default async function TopSignalsPage({
  searchParams,
}: {
  searchParams: {
    date?: string;
    sort_by?: "score" | "edge";
    scope?: SignalScope;
    competition_code?: string;
    days?: string;
    min_fair_odds?: string;
    max_fair_odds?: string;
    min_edge?: string;
    min_bookmakers?: string;
    quality?: "HIGH" | "MEDIUM" | "LOW" | "ALL";
  };
}) {
  const { date, competition_code: competitionCode } = searchParams;
  const sortBy = searchParams.sort_by === "edge" ? "edge" : "score";
  // `date` tiene prioridad sobre `scope` (ver backend); mostrarlo como su
  // propio modo evita que el selector de jornada contradiga al de fecha.
  const scope: SignalScope = date ? "all_upcoming" : (searchParams.scope ?? "current_round");
  const days = searchParams.days ? Number(searchParams.days) : 4;
  const minFairOdds = searchParams.min_fair_odds ? Number(searchParams.min_fair_odds) : undefined;
  const maxFairOdds = searchParams.max_fair_odds ? Number(searchParams.max_fair_odds) : undefined;
  const minEdge = searchParams.min_edge ? Number(searchParams.min_edge) : undefined;
  const minBookmakers = searchParams.min_bookmakers ? Number(searchParams.min_bookmakers) : undefined;
  const quality = searchParams.quality ?? "ALL";
  const hasFairOddsFilter = minFairOdds !== undefined || maxFairOdds !== undefined;
  const hasAnyFilter =
    Boolean(date) ||
    hasFairOddsFilter ||
    Boolean(competitionCode) ||
    scope !== "current_round" ||
    minEdge !== undefined ||
    minBookmakers !== undefined ||
    quality !== "ALL";

  const [predictions, competitions] = await Promise.all([
    getTopSignals({
      limit: 30,
      date,
      sortBy,
      scope,
      days,
      competitionCode,
      minFairOdds,
      maxFairOdds,
      minEdge,
      minBookmakers,
      quality,
    }).catch(() => []),
    getCompetitions().catch(() => []),
  ]);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Mejores señales</h1>
      <p className="mb-4 text-sm text-slate-500">
        {date
          ? `Solo partidos del ${new Date(`${date}T00:00:00`).toLocaleDateString("es-ES", {
              weekday: "long",
              day: "2-digit",
              month: "short",
            })}.`
          : scope === "all_upcoming"
            ? `De los próximos ${days} días (sin restricción de jornada).`
            : `${SCOPE_LABELS[scope]} de cada competición — nunca un partido de otra jornada aunque tenga más edge.`}{" "}
        Solo predicciones con mercado real y válido (cuota entre 1.45 y 6.0, edge no
        negativo, al menos una casa de apuestas respaldando el consenso) — ni una cuota irrisoria
        (1.02, edge casi nulo) ni un edge grande en un resultado muy improbable (cuota justa 8,
        mercado a 15) son predicciones prácticas para destacar, aunque matemáticamente haya
        diferencia.{" "}
        {sortBy === "edge"
          ? "Ordenado por edge de mayor a menor."
          : "Puntuación = probabilidad²× edge × confianza × calidad de datos — una cuota irrisoria (ej. 1.02) con edge casi nulo no sube aquí aunque la probabilidad del modelo sea altísima. La confianza ya incluye cuántas casas respaldan la cuota: un consenso de una única casa pesa menos que el de 5+."}{" "}
        {hasFairOddsFilter && (
          <>
            Filtrado a cuota justa del modelo entre {minFairOdds ?? "0"} y {maxFairOdds ?? "∞"}.
          </>
        )}
      </p>

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
        <label htmlFor="days" className="ml-1">
          (días si &quot;todas&quot;):
        </label>
        <input
          id="days"
          type="number"
          name="days"
          min="1"
          max="30"
          defaultValue={searchParams.days ?? "4"}
          className="w-16 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        />
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
          {competitions.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name}
            </option>
          ))}
        </select>
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
        <label htmlFor="sort_by" className="ml-2">
          Ordenar por:
        </label>
        <select
          id="sort_by"
          name="sort_by"
          defaultValue={sortBy}
          className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        >
          <option value="score">Puntuación (por defecto)</option>
          <option value="edge">Edge (mayor a menor)</option>
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
        <button type="submit" className="rounded border border-surface-border px-3 py-1 hover:bg-surface-raised">
          Aplicar
        </button>
        {hasAnyFilter && (
          <a href="/top-signals" className="text-slate-500 underline hover:text-slate-300">
            Quitar filtros
          </a>
        )}
      </form>

      <div className="overflow-hidden rounded-lg border border-surface-border">
        <table className="w-full text-sm">
          <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Partido</th>
              <th className="px-3 py-2">Fecha</th>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Modelo</th>
              <th className="px-3 py-2">Mercado</th>
              <th className="px-3 py-2">Cuota</th>
              <th className="px-3 py-2">Casas</th>
              <th className="px-3 py-2">Edge</th>
              <th className="px-3 py-2">Cuota justa</th>
              <th className="px-3 py-2">Calidad de datos</th>
              <th className="px-3 py-2">Calidad de mercado</th>
              <th className="px-3 py-2">Cuota actualizada</th>
            </tr>
          </thead>
          <tbody>
            {predictions.map((p) => (
              <tr key={p.id} className="border-t border-surface-border hover:bg-surface-raised/50">
                <td className="px-3 py-2 text-slate-200">
                  <Link href={`/signals/${p.id}`} className="hover:underline">
                    {p.match.home_team.canonical_name} vs {p.match.away_team.canonical_name}
                  </Link>
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {new Date(p.match.kickoff_utc).toLocaleDateString("es-ES", {
                    weekday: "short",
                    day: "2-digit",
                    month: "short",
                  })}
                </td>
                <td className="px-3 py-2 text-slate-300">{MARKET_DISPLAY_NAMES[p.market] ?? p.market}</td>
                <td className="px-3 py-2">{(p.model_probability * 100).toFixed(1)}%</td>
                <td className="px-3 py-2">
                  {p.market_probability !== null ? `${(p.market_probability * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="px-3 py-2">{p.market_odds !== null ? p.market_odds.toFixed(2) : "—"}</td>
                <td className="px-3 py-2 text-slate-400">{p.bookmakers_used ?? "—"}</td>
                <td className="px-3 py-2 text-edge-positive">
                  {p.edge !== null ? `+${(p.edge * 100).toFixed(1)} pp` : "—"}
                </td>
                <td className="px-3 py-2">{p.fair_odds.toFixed(2)}</td>
                <td className="px-3 py-2">
                  <SignalBadge tier={p.signal_tier} />
                </td>
                <td className="px-3 py-2">
                  <MarketQualityBadge quality={p.market_quality} />
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {p.is_stale_odds && <span className="mr-1 text-amber-400">⚠</span>}
                  {p.odds_age_minutes !== null ? `hace ${Math.round(p.odds_age_minutes)} min` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {predictions.length === 0 && (
          <div className="p-6 text-sm text-slate-500">
            No hay señales con mercado válido todavía para este filtro. Prueba con &quot;Todas las
            próximas&quot; o ejecuta{" "}
            <code className="rounded bg-black/40 px-1 py-0.5">football-edge update-odds</code>.
          </div>
        )}
      </div>
    </div>
  );
}
