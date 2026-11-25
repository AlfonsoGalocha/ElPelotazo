"use client";

import { useEffect, useState } from "react";
import { getSelectorDecisionsReport } from "@/lib/api";
import type { SelectorLearningBucket, SelectorLearningReport } from "@/types";
import { EmptyState } from "./EmptyState";

// Fase E (revision v2.8, seccion 10 del brief): "¿el selector elige
// bien?" -- compara, para cada partido donde "Mejores oportunidades" ya
// congelo una decision (ver backend/app/db/models/modeling.py::
// SelectorDecisionSnapshot), la seleccionada contra el resto de
// candidatas una vez que el partido liquida. Puramente prospectivo desde
// que esto se desplego: sin backfill, puede tardar dias/semanas en haber
// suficientes partidos liquidados para que las tasas de acierto
// signifiquen algo -- nunca se muestra un numero inventado mientras tanto.
function formatRate(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toFixed(1)}%`;
}

function BucketTable({ title, buckets, keyLabel }: { title: string; buckets: SelectorLearningBucket[]; keyLabel: string }) {
  if (buckets.length === 0) {
    return (
      <div>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
        <p className="text-sm text-slate-500">Sin datos liquidados todavía en este desglose.</p>
      </div>
    );
  }
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      <div className="overflow-hidden rounded-xl border border-surface-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-surface-alt/60 text-left text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">{keyLabel}</th>
              <th className="px-3 py-2">Liquidadas</th>
              <th className="px-3 py-2">Tasa de acierto</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((bucket) => (
              <tr key={bucket.key} className="border-t border-surface-border">
                <td className="px-3 py-2 text-slate-300">
                  {bucket.key === "excluded" ? "Excluida (ninguna posición)" : bucket.key}
                </td>
                <td className="px-3 py-2 text-slate-400">{bucket.n}</td>
                <td className="px-3 py-2 text-slate-300">{formatRate(bucket.win_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SelectorReliabilityBoard() {
  const [report, setReport] = useState<SelectorLearningReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSelectorDecisionsReport()
      .then(setReport)
      .catch(() => setError("No se pudo conectar con la API."));
  }, []);

  if (error) return <EmptyState title={error} />;
  if (report === null) {
    return <div className="h-48 animate-pulse rounded-xl border border-surface-border bg-card" />;
  }
  if (report.n_snapshots_total === 0) {
    return (
      <EmptyState
        title="Todavía no hay decisiones del selector congeladas"
        description="Ejecuta football-edge capture-selector-decisions (o espera al scheduler) para empezar a registrar cortes pre-partido."
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile label="Cortes registrados" value={String(report.n_snapshots_total)} />
        <StatTile label="Ya liquidados" value={String(report.n_snapshots_settled)} />
        <StatTile label="Pendientes de liquidar" value={String(report.n_snapshots_pending)} muted />
        <StatTile
          label="Acierto de la seleccionada"
          value={report.selected_n > 0 ? formatRate(report.selected_win_rate) : "—"}
          highlight
        />
      </div>

      {report.selected_n > 0 && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
          <p className="text-sm text-slate-300">
            De {report.selected_n} decisiones liquidadas, en {report.missed_opportunities_n} la seleccionada perdió
            mientras otra candidata (peor posición o excluida) sí ganaba
            {report.missed_opportunities_rate !== null && (
              <> ({formatRate(report.missed_opportunities_rate)} de las liquidadas donde perdió, con otra opción disponible).</>
            )}
          </p>
        </div>
      )}

      {report.n_snapshots_settled === 0 && (
        <p className="text-sm text-slate-500">
          Ningún corte registrado ha liquidado todavía -- las tasas de acierto por posición/categoría aparecerán aquí
          en cuanto los primeros partidos capturados terminen y se liquiden.
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <BucketTable title="Por posición en el ranking" buckets={report.by_rank_position} keyLabel="Posición" />
        <BucketTable title="Por categoría (A+/A/B/C/D)" buckets={report.by_category} keyLabel="Categoría" />
      </div>
    </div>
  );
}

function StatTile({ label, value, highlight, muted }: { label: string; value: string; highlight?: boolean; muted?: boolean }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-3">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={
          "mt-1 text-lg font-semibold " + (highlight ? "text-accent" : muted ? "text-slate-500" : "text-slate-100")
        }
      >
        {value}
      </div>
    </div>
  );
}
