import Link from "next/link";
import type { PublicationSelection } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";

const labels: Record<string, string> = {
  won: "Acertado", lost: "Fallado", void: "Anulado",
  unsettled: "Pendiente", unverifiable: "Sin verificar",
};

export function PublicationRows({ rows }: { rows: PublicationSelection[] }) {
  if (!rows.length) return <p className="text-sm text-slate-400">Todavía no hay capturas para esta consulta.</p>;
  return (
    <div className="space-y-3">
      {rows.map(row => (
        <details key={`${row.match_id}-${row.market}`} className="rounded-xl border border-surface-border p-4">
          <summary className="cursor-pointer text-slate-200">
            {row.match} · {MARKET_DISPLAY_NAMES[row.market] ?? row.market} · {labels[row.status] ?? row.status}
          </summary>
          <p className="mt-2 text-sm text-slate-400">
            {row.competition} · Jornada {row.matchday ?? "—"} · {row.kickoff.slice(0, 10)} · Modelo {row.model_version_id}
          </p>
          <p className="text-sm text-slate-300">
            Probabilidad original {(row.probability * 100).toFixed(1)} % · Calibrada {row.calibrated == null ? "—" : `${(row.calibrated * 100).toFixed(1)} %`} · Cuota {row.odds?.toFixed(2) ?? "—"}
          </p>
          <p className="text-sm text-slate-400">Captura UTC {row.captured_at.replace("T", " ").slice(0, 16)} · Marcador {row.score?.[0] == null ? "—" : row.score.join("–")}</p>
          {row.profit_units !== undefined && <p className="text-sm">Resultado teórico {row.profit_units.toFixed(2)} u</p>}
          {row.alternatives?.map(p => (
            <p key={p.market} className="text-sm text-slate-400">
              {MARKET_DISPLAY_NAMES[p.market] ?? p.market} · {(p.probability * 100).toFixed(1)} %
            </p>
          ))}
          {row.probability_error != null && <p className="text-sm text-slate-400">Error cuadrático del pronóstico {row.probability_error.toFixed(3)}</p>}
          <Link className="text-sm text-accent underline" href={`/prediction-history/${row.match_id}`}>Consultar captura original</Link>
        </details>
      ))}
    </div>
  );
}
