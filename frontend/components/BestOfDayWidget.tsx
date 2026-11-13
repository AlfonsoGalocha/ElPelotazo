import Link from "next/link";
import { getBestOfDay } from "@/lib/api";
import { MARKET_DISPLAY_NAMES, marketFamily } from "@/types";
import SignalBadge from "./SignalBadge";
import MarketQualityBadge from "./MarketQualityBadge";

const FAMILY_LABELS: Record<string, string> = {
  goals: "⚽ Goles",
  cards: "🟨 Tarjetas",
  corners: "🚩 Córners",
};

// "Mejor señal de la jornada", con el filtro MAS estricto del sistema (ver
// /predictions/best-of-day). Puede no haber ninguna -- eso es correcto
// (menos señales bien justificadas antes que forzar volumen), nunca se
// rebaja el filtro para mostrar algo.
//
// Bug real reportado por el usuario: se llamaba "...del DIA" pero el scope
// real es la JORNADA ACTUAL de cada liga (puede incluir partidos de dentro
// de una o dos semanas, p.ej. durante un parón de selecciones) -- el titulo
// prometia "hoy" cuando el partido mostrado podia ser del domingo que viene.
// Corregido a "de la jornada", que es lo que el endpoint realmente filtra.
//
// Pedido de rediseño: elemento visual destacado del dashboard (pero sin
// ocupar tanto espacio como antes) -- calidad de MERCADO y confianza del
// MODELO mostradas por separado (nunca mezcladas en un unico numero: son
// dos conceptos distintos, ver MarketQualityBadge.tsx/SignalBadge.tsx).
export default async function BestOfDayWidget() {
  const { prediction, explanation } = await getBestOfDay().catch(() => ({
    prediction: null,
    explanation: ["No se pudo consultar la mejor señal del día."],
  }));

  return (
    <div className="overflow-hidden rounded-lg border border-opportunity/30 bg-gradient-to-br from-opportunity/[0.07] to-surface-raised">
      <div className="flex items-center gap-2 border-b border-opportunity/20 px-4 py-2">
        <span className="text-base text-opportunity">★</span>
        <span className="text-xs font-semibold uppercase tracking-wide text-opportunity">
          Mejor oportunidad de la jornada
        </span>
      </div>

      {!prediction && (
        <div className="px-4 py-4 text-sm text-slate-400">
          Ninguna predicción de la jornada actual (de cualquier liga) cumple todos los requisitos
          mínimos (mercado real, cuota reciente, bookmakers suficientes, modelo calibrado, edge
          mínimo estricto, sin contradicción con otro mercado del mismo partido). No es un error: se
          prefiere no destacar nada antes que forzar una señal débil.
        </div>
      )}

      {prediction && (
        <Link href={`/matches/${prediction.match.id}`} className="block px-4 py-3 hover:bg-black/10">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-slate-500">
                {prediction.match.competition.name} ·{" "}
                {new Date(prediction.match.kickoff_utc).toLocaleDateString("es-ES", {
                  weekday: "short",
                  day: "2-digit",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
              <div className="mt-0.5 text-base font-bold text-slate-100">
                {prediction.match.home_team.canonical_name}
                <span className="mx-1.5 text-slate-500">vs</span>
                {prediction.match.away_team.canonical_name}
              </div>
              <div className="mt-1 text-xs text-slate-300">
                {FAMILY_LABELS[marketFamily(prediction.market)]} ·{" "}
                <span className="font-medium text-slate-100">
                  {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
                </span>
              </div>
            </div>

            <div className="flex flex-col items-end gap-0.5">
              <span className="text-xl font-bold text-slate-100">
                {(prediction.model_probability * 100).toFixed(1)}%
              </span>
              <span className="text-[11px] text-slate-500">probabilidad del modelo</span>
              <div className="mt-0.5 flex items-center gap-2 text-xs">
                {prediction.market_odds != null && (
                  <span className="text-slate-400">cuota {prediction.market_odds.toFixed(2)}</span>
                )}
                {prediction.edge != null && (
                  <span className="font-semibold text-edge-positive">
                    +{(prediction.edge * 100).toFixed(1)}pp edge
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <MarketQualityBadge quality={prediction.market_quality} />
            <SignalBadge tier={prediction.signal_tier} />
          </div>
        </Link>
      )}

      <details className="px-4 pb-3">
        <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
          Ver por qué
        </summary>
        <ul className="mt-1 flex flex-col gap-0.5 text-[11px] text-slate-500">
          {explanation.map((line, i) => (
            <li key={i}>· {line}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}
