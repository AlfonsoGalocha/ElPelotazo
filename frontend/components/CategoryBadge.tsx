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

// v2.8.2 (seccion 5): `categoryStatus` distingue "completada" (cache
// fresca, se puede confiar en el color/etiqueta) de "desactualizada" (la
// cuota/calibracion cambiaron desde que se capturo -- NUNCA se muestra
// el color favorable como si siguiera vigente, se apaga a gris con un
// aviso) y "pendiente" (nunca se capturo, p.ej. partido fuera de la
// ventana de 48h del scheduler) -- nunca se infiere ninguna de las tres
// a partir de `category === null` a secas.
const PENDING_TITLE =
  "Categoria todavia no calculada para esta señal (fuera de la ventana de captura del scheduler, o pendiente del proximo corte).";

export default function CategoryBadge({
  category,
  categoryStatus = "completada",
}: {
  category: OpportunityCategory | null;
  categoryStatus?: "completada" | "desactualizada" | "pendiente";
}) {
  if (category === null || categoryStatus === "pendiente") {
    return (
      <span
        className="inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium tracking-wide bg-slate-500/10 text-slate-500 border-slate-500/20"
        title={PENDING_TITLE}
      >
        CATEGORIA PENDIENTE
      </span>
    );
  }
  const stale = categoryStatus === "desactualizada";
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium tracking-wide ${
        stale ? "bg-slate-500/10 text-slate-400 border-slate-500/20" : STYLES[category]
      }`}
      title={
        stale
          ? `${TITLES[category]} (OJO: la cuota o la calibracion cambiaron desde que se calculo esta categoria -- puede no reflejar el estado actual.)`
          : TITLES[category]
      }
    >
      CATEGORIA {category}
      {stale ? " ⚠" : ""}
    </span>
  );
}
