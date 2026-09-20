import type { AnomalyFlag } from "@/types";

// Fase 6 (seccion 18/25): colores SOLO transmiten estado -- ambar para
// avisos, nunca "todo verde" solo porque hay edge positivo (seccion 25).
// CONTRADICCION casi nunca deberia verse en produccion (el fix de
// renormalizacion de mercados excluyentes la hace practicamente
// imposible), pero se muestra en rojo si aparece: es una senhal de que
// algo no cuadra, no un aviso menor.
const STYLES: Record<AnomalyFlag, string> = {
  CONTRADICCION: "bg-red-500/15 text-red-400 border-red-500/30",
  SENAL_BAJA_FIABILIDAD: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  OUTLIER: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  HIGH_PROBABILITY_LOW_VALUE: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

const LABELS: Record<AnomalyFlag, string> = {
  CONTRADICCION: "CONTRADICCIÓN ENTRE MERCADOS",
  SENAL_BAJA_FIABILIDAD: "BAJA FIABILIDAD",
  OUTLIER: "CUOTA OUTLIER",
  HIGH_PROBABILITY_LOW_VALUE: "PROBABILIDAD ALTA, POCO VALOR",
};

export default function AnomalyBadges({ flags }: { flags: AnomalyFlag[] }) {
  if (!flags || flags.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {flags.map((flag) => (
        <span
          key={flag}
          className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium tracking-wide ${STYLES[flag]}`}
          title={
            flag === "HIGH_PROBABILITY_LOW_VALUE"
              ? "Probabilidad alta del modelo, pero la cuota ya refleja lo obvio (edge minimo)."
              : flag === "OUTLIER"
                ? "Cuota respaldada por pocas casas o con mucha dispersion entre ellas."
                : flag === "SENAL_BAJA_FIABILIDAD"
                  ? "Confianza o calidad de datos por debajo del umbral normal."
                  : "Otra prediccion del mismo partido, en un mercado mutuamente excluyente, tambien supera el 50%."
          }
        >
          {LABELS[flag]}
        </span>
      ))}
    </div>
  );
}
