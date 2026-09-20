import { notFound } from "next/navigation";
import Link from "next/link";
import { getPredictionDetail } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";
import SignalBadge from "@/components/SignalBadge";
import MarketQualityBadge from "@/components/MarketQualityBadge";

export default async function SignalDetailPage({ params }: { params: { id: string } }) {
  const predictionId = Number(params.id);
  if (Number.isNaN(predictionId)) notFound();

  const signal = await getPredictionDetail(predictionId).catch(() => null);
  if (!signal) notFound();

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
          {new Date(signal.match.kickoff_utc).toLocaleString("es-ES")} ·{" "}
          {MARKET_DISPLAY_NAMES[signal.market] ?? signal.market}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Modelo" value={`${(signal.model_probability * 100).toFixed(1)}%`} />
        <StatTile
          label="Mercado"
          value={signal.market_probability !== null ? `${(signal.market_probability * 100).toFixed(1)}%` : "—"}
        />
        <StatTile label="Cuota" value={signal.market_odds !== null ? signal.market_odds.toFixed(2) : "—"} />
        <StatTile label="Cuota justa" value={signal.fair_odds.toFixed(2)} />
        <StatTile
          label="Edge"
          value={signal.edge !== null ? `${signal.edge >= 0 ? "+" : ""}${(signal.edge * 100).toFixed(1)} pp` : "—"}
        />
        <StatTile label="Casas" value={String(signal.bookmakers_used ?? "—")} />
        <StatTile
          label="Dispersión"
          value={
            signal.market_odds_min !== null && signal.market_odds_max !== null
              ? `${signal.market_odds_min.toFixed(2)} – ${signal.market_odds_max.toFixed(2)}`
              : "—"
          }
        />
        <StatTile
          label="Actualizado"
          value={signal.odds_age_minutes !== null ? `hace ${Math.round(signal.odds_age_minutes)} min` : "—"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <SignalBadge tier={signal.signal_tier} />
        <MarketQualityBadge quality={signal.market_quality} />
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
                        <span className="text-xs text-slate-500">en el consenso</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            La cuota usada para calcular el edge es la MEDIANA del consenso ({signal.market_odds_median?.toFixed(2) ?? "—"}
            ), no la mejor cuota disponible ni una casa aislada — ver{" "}
            <code className="rounded bg-black/40 px-1 py-0.5">backend/app/market/consensus.py</code>.
          </p>
        </section>
      )}

      <p className="text-xs text-slate-600">
        Herramienta de análisis estadístico. Ninguna cifra de esta página constituye una garantía de resultado.
      </p>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-3">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-100">{value}</div>
    </div>
  );
}
