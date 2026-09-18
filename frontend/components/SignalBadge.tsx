import type { SignalTier } from "@/types";

const STYLES: Record<SignalTier, string> = {
  HIGH_DATA_SUPPORT: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  MEDIUM_DATA_SUPPORT: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  LOW_DATA_SUPPORT: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

const LABELS: Record<SignalTier, string> = {
  HIGH_DATA_SUPPORT: "DATOS ALTA CALIDAD",
  MEDIUM_DATA_SUPPORT: "DATOS MEDIA CALIDAD",
  LOW_DATA_SUPPORT: "DATOS BAJA CALIDAD",
};

// A peticion explicita: la etiqueta "DATOS MEDIA CALIDAD" es ruido visual
// (la inmensa mayoria de predicciones caen ahi) y no aporta nada
// accionable. Solo se muestra un badge para los extremos (alta o baja
// calidad de datos); "media" no se muestra (no es ni una senhal buena ni
// una alerta, es el caso normal).
export default function SignalBadge({ tier }: { tier: SignalTier }) {
  if (tier === "MEDIUM_DATA_SUPPORT") return null;
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-[10px] font-medium tracking-wide ${STYLES[tier]}`}>
      {LABELS[tier]}
    </span>
  );
}
