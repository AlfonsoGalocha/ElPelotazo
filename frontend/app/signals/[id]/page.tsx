import { notFound } from "next/navigation";
import Link from "next/link";
import { getPredictionDetail, getSelectorDecisionsForMatch } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";
import MarketQualityBadge from "@/components/MarketQualityBadge";
import CategoryBadge from "@/components/CategoryBadge";
import ContradictionBadge from "@/components/ContradictionBadge";
import SelectorExplanation from "@/components/SelectorExplanation";

export default async function SignalDetailPage({ params }: { params: { id: string } }) {
  const predictionId = Number(params.id);
  if (Number.isNaN(predictionId)) notFound();

  const signal = await getPredictionDetail(predictionId).catch(() => null);
  if (!signal) notFound();
  const selectorDecisions = await getSelectorDecisionsForMatch(signal.match.id).catch(() => null);
  const probability = signal.calibrated_probability ?? signal.model_probability;
  const gap = signal.market_probability === null ? null : probability - signal.market_probability;
  const signed = (value: number) => (value >= 0 ? "+" : "") + (value * 100).toFixed(1);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">
          <Link href={`/matches/${signal.match.id}`} className="hover:underline">
            {signal.match.competition.name}
          </Link>
        </div>
        <h1 className="text-2xl font-semibold text-slate-100">
          {signal.match.home_team.canonical_name} <span className="text-slate-500">vs</span>{" "}
          {signal.match.away_team.canonical_name}
        </h1>
        <div className="text-sm text-slate-500">
          {formatMatchDateTime(signal.match.kickoff_utc)} ·{" "}
          {MARKET_DISPLAY_NAMES[signal.market] ?? signal.market}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile
          label="Probabilidad calibrada"
          value={
            signal.calibrated_probability !== null
              ? `${(signal.calibrated_probability * 100).toFixed(1)}%`
              : `${(signal.model_probability * 100).toFixed(1)}% (sin calibrar)`
          }
          highlight
        />
        <StatTile label="Cuota" value={signal.market_odds !== null ? signal.market_odds.toFixed(2) : "—"} />
        <StatTile
          label="EV estimado"
          value={signal.ev_calibrated !== null ? `${signed(signal.ev_calibrated)}%` : "—"}
        />
        <StatTile
          label="EV conservador"
          value={signal.ev_conservative !== null ? `${signed(signal.ev_conservative)}%` : "—"}
        />
        <StatTile
          label="Mercado (prob.)"
          value={signal.market_probability !== null ? `${(signal.market_probability * 100).toFixed(1)}%` : "—"}
        />
        <StatTile label="Casas" value={String(signal.bookmakers_used ?? "—")} />
        <StatTile label="Cuota justa" value={probability > 0 ? (1 / probability).toFixed(2) : "—"} />
        <StatTile
          label="Dispersión"
          value={
            signal.market_odds_min !== null && signal.market_odds_max !== null
              ? `${signal.market_odds_min.toFixed(2)} – ${signal.market_odds_max.toFixed(2)}`
              : "—"
          }
        />
        <StatTile
          label="Origen del proveedor"
          value={signal.odds_age_minutes !== null ? `hace ${Math.round(signal.odds_age_minutes)} min` : "—"}
        />
        <StatTile
          label="Última consulta de esta captura"
          value={signal.odds_fetched_at ? formatMatchDateTime(signal.odds_fetched_at) : "Sin registro"}
        />
        <StatTile
          label="Diferencia vs. consenso"
          value={gap !== null ? `${signed(gap)} pp` : "—"}
          muted
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SignalBadge tier={signal.signal_tier} />
        <MarketQualityBadge quality={signal.market_quality} />
        <CategoryBadge category={signal.category} categoryStatus={signal.category_status} />
        <ContradictionBadge level={signal.contradiction_level} />
        {signal.is_stale_odds && (
          <span className="rounded border border-amber-500/30 bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-400">
            ⚠ CUOTA DESACTUALIZADA
          </span>
        )}
      </div>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
          ¿Por qué aparece esta señal?
        </h2>
        <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
          <ul className="flex flex-col gap-2 text-sm text-slate-300">
            {signal.explanation_summary.map((line, i) => (
              <li key={i}>· {line}</li>
            ))}
          </ul>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Qué evaluó el selector y por qué
        </h2>
        <SelectorExplanation data={selectorDecisions} currentPredictionId={signal.id} />
      </section>

      {signal.bookmaker_odds.length > 0 && (
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            Cuotas por casa de apuestas
          </h2>
          <div className="overflow-hidden rounded-lg border border-surface-border">
            <table className="w-full text-sm">
              <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">Casa</th>
                  <th className="px-3 py-2">Cuota</th>
                  <th className="px-3 py-2">Diferencia vs. consenso</th>
                  <th className="px-3 py-2">Snapshot</th>
                  <th className="px-3 py-2">Estado</th>
                </tr>
              </thead>
              <tbody>
                {signal.bookmaker_odds.map((row) => (
                  <tr key={row.bookmaker} className="border-t border-surface-border">
                    <td className="px-3 py-2 text-slate-200">{row.bookmaker}</td>
                    <td className="px-3 py-2">{row.price.toFixed(2)}</td>
                    <td className="px-3 py-2">
                      {row.diff_from_consensus !== null
                        ? `${row.diff_from_consensus >= 0 ? "+" : ""}${row.diff_from_consensus.toFixed(2)}`
                        : "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-400">{row.snapshot_type}</td>
                    <td className="px-3 py-2">
                      {row.is_outlier ? (
                        <span className="rounded border border-rose-500/30 bg-rose-500/15 px-2 py-0.5 text-[10px] font-medium text-rose-400">
                          DESCARTADA DEL CONSENSO (OUTLIER)
                        </span>
                      ) : (
                        <span className="text-xs text-slate-500">{row.is_outlier === null ? "Sin clasificación archivada" : "No marcada como atípica"}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            {signal.bookmaker_odds_from_archive
              ? "Cuotas archivadas al generar este pronóstico. El EV de esta ficha usa la cuota de consenso. Oportunidades calcula su EV con la cuota de la casa seleccionada."
              : "Cuotas operativas de referencia. Esta predicción no conserva las cuotas individuales de su captura."}
          </p>
        </section>
      )}

      <p className="text-xs text-slate-600">
        Herramienta de análisis estadístico. Ninguna cifra de esta página constituye una garantía de resultado.
      </p>
    </div>
  );
}

function StatTile({
  label,
  value,
  highlight,
  muted,
}: {
  label: string;
  value: string;
  highlight?: boolean;
  muted?: boolean;
}) {
  return (
    <div
      className={
        "rounded-lg border p-3 " +
        (highlight ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-raised")
      }
    >
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className={"text-lg font-semibold " + (muted ? "text-slate-500" : "text-slate-100")}>{value}</div>
    </div>
  );
}
