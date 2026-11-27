import type { ContradictionLevel } from "@/types";

// Nivel de contradiccion entre la probabilidad del modelo y la
// frecuencia historica real (ver backend/app/prediction/anomaly.py
// ::classify_contradiction, bandas de z-score 1.0/2.0/3.0 σ). Hasta
// v2.8.3 este dato llegaba al frontend (`Prediction.contradiction_level`)
// pero no se mostraba en ningun sitio -- CategoryBadge ya refleja su
// efecto (FUERTE fuerza categoria D) pero sin nombrar la causa.
const STYLES: Record<ContradictionLevel, string> = {
  SIN_CONTRADICCION: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  LEVE: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  MODERADA: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  FUERTE: "bg-rose-500/15 text-rose-400 border-rose-500/30",
  EVIDENCIA_INSUFICIENTE: "bg-slate-500/10 text-slate-400 border-slate-500/20",
};

const LABELS: Record<ContradictionLevel, string> = {
  SIN_CONTRADICCION: "Sin contradicción",
  LEVE: "Contradicción leve",
  MODERADA: "Contradicción moderada",
  FUERTE: "Contradicción fuerte",
  EVIDENCIA_INSUFICIENTE: "Evidencia insuficiente",
};

const TITLES: Record<ContradictionLevel, string> = {
  SIN_CONTRADICCION: "La probabilidad del modelo y la frecuencia histórica real de esta liga/mercado coinciden.",
  LEVE: "Diferencia leve entre la probabilidad del modelo y la frecuencia histórica real -- no impide la categoría B o superior.",
  MODERADA: "Diferencia moderada entre la probabilidad del modelo y la frecuencia histórica real -- limita la categoría a C.",
  FUERTE: "Diferencia fuerte entre la probabilidad del modelo y la frecuencia histórica real -- fuerza la categoría D aunque el resto de requisitos se cumplan.",
  EVIDENCIA_INSUFICIENTE: "No hay suficiente historial liquidado de esta liga/mercado para contrastar la probabilidad del modelo.",
};

export default function ContradictionBadge({ level }: { level: ContradictionLevel | null }) {
  if (level === null) {
    return <span className="text-xs text-slate-500">—</span>;
  }
  return (
    <span
      className={`inline-block rounded border px-1.5 py-0.5 text-[9px] font-medium tracking-wide ${STYLES[level]}`}
      title={TITLES[level]}
    >
      {LABELS[level].toUpperCase()}
    </span>
  );
}
