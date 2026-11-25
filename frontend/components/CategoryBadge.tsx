import type { OpportunityCategory } from "@/types";

// Fase D (revision v2.8): categoria determinista A+/A/B/C/D (ver
// backend/app/prediction/opportunity_category.py) -- color en escala de
// confianza (verde=mejor, rojo=peor), nunca "todo verde" solo porque hay
// edge positivo (mismo criterio ya aplicado en AnomalyBadge/
// MarketQualityBadge).
const STYLES: Record<OpportunityCategory, string> = {
  "A+": "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  A: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  B: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  C: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  D: "bg-rose-500/15 text-rose-400 border-rose-500/30",
};

const TITLES: Record<OpportunityCategory, string> = {
  "A+": "Pasa \"Mejores oportunidades\", fiabilidad historica exacta de esta competicion/mercado, sin contradiccion ni anomalias, intervalo de incertidumbre estrecho y muestra historica amplia.",
  A: "Pasa \"Mejores oportunidades\", fiabilidad historica exacta de esta competicion/mercado, sin contradiccion ni anomalias.",
  B: "Pasa \"Mejores oportunidades\" pero con fiabilidad historica heredada (no especifica de esta competicion exacta) o una contradiccion leve entre la probabilidad del modelo y la frecuencia historica real.",
  C: "Senhal valida pero no alcanza el nivel de \"Mejores oportunidades\" (falta calibracion/incertidumbre/fiabilidad historica), o la evidencia historica real es insuficiente o moderadamente contradictoria.",
  D: "No pasa el filtro general de calidad, o hay una contradiccion fuerte entre la probabilidad del modelo y la frecuencia historica real -- nunca deberia tratarse como una recomendacion.",
};

// `null`: el llamador no calculo la categoria (endpoints de lista -- ver
// comentario de costes en backend/app/api/routes/predictions.py). Nunca
// se muestra un guion como si fuera "D": null y "sin categoria" son
// estados distintos.
export default function CategoryBadge({ category }: { category: OpportunityCategory | null }) {
  if (category === null) return null;
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium tracking-wide ${STYLES[category]}`}
      title={TITLES[category]}
    >
      CATEGORIA {category}
    </span>
  );
}
