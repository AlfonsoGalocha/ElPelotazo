import { getSummary } from "@/lib/api";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xl font-semibold text-slate-100">{value}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </div>
  );
}

// Pedido de rediseño: "resumen de jornada" en lo alto del dashboard --
// fecha, partidos pendientes, competiciones, oportunidades detectadas y
// cuantas de esas tienen datos de alta calidad. Mismo criterio real que
// usa el resto de la app (ver /predictions/summary), nunca un numero
// aparte inventado solo para esta cabecera.
export default async function SummaryBar() {
  const summary = await getSummary().catch(() => null);
  if (!summary) return null;

  const today = new Date(summary.date).toLocaleDateString("es-ES", {
    weekday: "long",
    day: "2-digit",
    month: "long",
  });

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-surface-border bg-surface-raised px-4 py-3">
      <span className="text-sm capitalize text-slate-400">{today}</span>
      <div className="flex flex-wrap gap-6">
        <Stat label="Partidos pendientes" value={String(summary.matches_pending)} />
        <Stat label="Competiciones" value={String(summary.competitions_count)} />
        <Stat label="Oportunidades detectadas" value={String(summary.opportunities_count)} />
        <Stat label="De alta calidad" value={String(summary.high_quality_count)} />
      </div>
    </div>
  );
}
