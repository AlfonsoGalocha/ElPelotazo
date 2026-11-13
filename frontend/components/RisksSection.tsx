import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";

interface Risk {
  label: string;
  detail: string;
  markets: string[];
}

const MIN_LIQUID_BOOKMAKERS = 3;

// Pedido de rediseño ("Riesgos detectados"): nunca se inventa un riesgo
// ni informacion que no este respaldada por los datos disponibles --
// cada fila de abajo viene de un campo REAL ya calculado en el backend
// (anomaly_flags, signal_tier, market_quality, is_stale_odds,
// calibrated_probability). "Posibles cambios de alineacion" se muestra
// aparte, como aviso honesto de que esta app no rastrea alineaciones (sin
// fuente de datos conectada), nunca como un riesgo "detectado".
function detectRisks(predictions: Prediction[]): Risk[] {
  const risks: Risk[] = [];
  const withMarket = predictions.filter((p) => p.has_market);

  const contradictions = predictions.filter((p) => p.anomaly_flags.includes("CONTRADICCION"));
  if (contradictions.length > 0) {
    risks.push({
      label: "Contradicción entre mercados",
      detail:
        "Más de una predicción del mismo partido, en mercados mutuamente excluyentes, supera el 50% a la vez.",
      markets: contradictions.map((p) => MARKET_DISPLAY_NAMES[p.market] ?? p.market),
    });
  }

  const lowConfidence = predictions.filter((p) => p.confidence < 0.5);
  if (lowConfidence.length > 0) {
    risks.push({
      label: "Confianza del modelo baja",
      detail: "La fiabilidad estadística estimada del modelo para estos mercados está por debajo de lo normal.",
      markets: lowConfidence.map((p) => MARKET_DISPLAY_NAMES[p.market] ?? p.market),
    });
  }

  const lowDataQuality = predictions.filter((p) => p.data_quality < 0.5);
  if (lowDataQuality.length > 0) {
    risks.push({
      label: "Datos de los equipos incompletos",
      detail: "Alguno de los dos equipos tiene poco historial reciente disponible para calcular sus features.",
      markets: lowDataQuality.map((p) => MARKET_DISPLAY_NAMES[p.market] ?? p.market),
    });
  }

  const illiquid = withMarket.filter(
    (p) => p.market_quality === "LOW" || (p.bookmakers_used ?? 0) < MIN_LIQUID_BOOKMAKERS
  );
  if (illiquid.length > 0) {
    risks.push({
      label: "Mercado poco líquido",
      detail: `Menos de ${MIN_LIQUID_BOOKMAKERS} casas de apuestas respaldan la cuota, o hay mucha dispersión entre ellas.`,
      markets: illiquid.map((p) => MARKET_DISPLAY_NAMES[p.market] ?? p.market),
    });
  }

  const stale = withMarket.filter((p) => p.is_stale_odds);
  if (stale.length > 0) {
    risks.push({
      label: "Cuotas desactualizadas",
      detail: "El precio de mercado registrado tiene más antigüedad de la recomendada.",
      markets: stale.map((p) => MARKET_DISPLAY_NAMES[p.market] ?? p.market),
    });
  }

  const uncalibrated = withMarket.filter((p) => p.calibrated_probability === null);
  if (uncalibrated.length > 0) {
    risks.push({
      label: "Sin calibración suficiente",
      detail: "Todavía no hay histórico de resultados liquidados suficiente para calibrar la probabilidad de estos mercados.",
      markets: uncalibrated.map((p) => MARKET_DISPLAY_NAMES[p.market] ?? p.market),
    });
  }

  return risks;
}

export default function RisksSection({ predictions }: { predictions: Prediction[] }) {
  const risks = detectRisks(predictions);

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Riesgos detectados</h2>

      {risks.length === 0 ? (
        <p className="text-sm text-slate-400">
          Sin riesgos detectados en los datos disponibles para este partido (mercado líquido, sin
          contradicciones, confianza y calidad de datos normales).
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {risks.map((risk) => (
            <li key={risk.label} className="rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2">
              <div className="text-sm font-medium text-amber-400">{risk.label}</div>
              <div className="text-xs text-slate-400">{risk.detail}</div>
              <div className="mt-1 text-xs text-slate-500">Afecta a: {risk.markets.join(", ")}</div>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-slate-500">
        Nota: esta aplicación no tiene conectada ninguna fuente de alineaciones o lesiones todavía —
        no se puede avisar de cambios de alineación hasta que exista esa integración.
      </p>
    </section>
  );
}
