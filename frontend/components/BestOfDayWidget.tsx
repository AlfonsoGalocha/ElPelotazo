import Link from "next/link";
import { getBestOfDay } from "@/lib/api";
import { MARKET_DISPLAY_NAMES, marketFamily } from "@/types";

const FAMILY_LABELS: Record<string, string> = {
  goals: "⚽ Goles",
  cards: "🟨 Tarjetas",
  corners: "🚩 Córners",
};

// Fase 6 (seccion 19): "Mejor Señal del Día", con el filtro MAS estricto
// del sistema (ver /predictions/best-of-day). Puede no haber ninguna --
// eso es correcto (seccion 1: menos señales bien justificadas antes que
// forzar volumen), nunca se rebaja el filtro para mostrar algo.
export default async function BestOfDayWidget() {
  const { prediction, explanation } = await getBestOfDay().catch(() => ({
    prediction: null,
    explanation: ["No se pudo consultar la mejor señal del día."],
  }));

  return (
    <div className="rounded-lg border border-amber-500/20 bg-surface-raised p-4">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-amber-400">
        <span>★</span> Mejor señal del día
      </div>
      {!prediction && (
        <div className="text-sm text-slate-400">
          Ninguna predicción de la jornada actual cumple hoy todos los requisitos mínimos (mercado
          real, cuota reciente, bookmakers suficientes, modelo calibrado, edge mínimo estricto, sin
          contradicción con otro mercado del mismo partido). No es un error: se prefiere no destacar
          nada antes que forzar una señal débil.
        </div>
      )}
      {prediction && (
        <Link
          href={`/matches/${prediction.match.id}`}
          className="flex flex-col gap-2 rounded border border-surface-border px-3 py-3 hover:bg-black/20"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-slate-100">
              {prediction.match.home_team.canonical_name}{" "}
              <span className="text-slate-500">vs</span> {prediction.match.away_team.canonical_name}
              <span className="ml-2 text-xs text-slate-500">
                {new Date(prediction.match.kickoff_utc).toLocaleDateString("es-ES", {
                  weekday: "short",
                  day: "2-digit",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
            <div className="flex items-center gap-2 text-sm">
              {prediction.market_odds != null && (
                <span className="text-slate-400">cuota {prediction.market_odds.toFixed(2)}</span>
              )}
              <span className="font-semibold text-slate-100">
                {(prediction.model_probability * 100).toFixed(1)}%
              </span>
              {prediction.edge != null && (
                <span className="text-edge-positive">+{(prediction.edge * 100).toFixed(1)}pp</span>
              )}
            </div>
          </div>
          <div className="text-xs text-slate-500">
            {FAMILY_LABELS[marketFamily(prediction.market)]} ·{" "}
            {MARKET_DISPLAY_NAMES[prediction.market] ?? prediction.market}
          </div>
        </Link>
      )}
      <ul className="mt-2 flex flex-col gap-0.5 text-[11px] text-slate-500">
        {explanation.map((line, i) => (
          <li key={i}>· {line}</li>
        ))}
      </ul>
    </div>
  );
}
