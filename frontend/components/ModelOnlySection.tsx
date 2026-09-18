import Link from "next/link";
import type { Prediction } from "@/types";
import { MARKET_DISPLAY_NAMES, marketFamily } from "@/types";

const FAMILY_LABELS: Record<string, string> = {
  goals: "⚽ Goles",
  cards: "🟨 Tarjetas",
  corners: "🚩 Córners",
};

// Predicciones del modelo SIN mercado (tarjetas/corners siempre; goles
// cuando aun no hay cuota). Existen y son validas, pero nunca deben
// competir con las senhales que si tienen mercado: se muestran aparte,
// etiquetadas explicitamente en vez de mezclarse silenciosamente.
export default function ModelOnlySection({ predictions }: { predictions: Prediction[] }) {
  return (
    <div id="sin-mercado" className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Predicción del modelo — sin mercado
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Solo probabilidad del modelo: tarjetas y córners no tienen cuotas reales en las fuentes de
        datos usadas, y algunos mercados de goles pueden no tenerlas todavía. Sin cuota de mercado no
        hay edge que calcular, así que estas predicciones nunca compiten en "Mejores señales".
      </p>
      <div className="flex flex-col gap-2">
        {predictions.map((p) => (
          <Link
            key={p.id}
            href={`/matches/${p.match.id}`}
            className="flex items-center justify-between rounded border border-surface-border px-3 py-2 text-sm hover:bg-black/20"
          >
            <div>
              <div className="text-slate-200">
                {p.match.home_team.canonical_name} vs {p.match.away_team.canonical_name}
              </div>
              <div className="text-xs text-slate-500">
                {FAMILY_LABELS[marketFamily(p.market)]} · {MARKET_DISPLAY_NAMES[p.market] ?? p.market}
              </div>
            </div>
            <span className="font-semibold text-slate-100">{(p.model_probability * 100).toFixed(1)}%</span>
          </Link>
        ))}
        {predictions.length === 0 && (
          <div className="text-sm text-slate-500">Sin predicciones sin mercado por ahora.</div>
        )}
      </div>
    </div>
  );
}
