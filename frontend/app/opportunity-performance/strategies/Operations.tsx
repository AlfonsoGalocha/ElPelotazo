import type { ShadowOperations } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";

const states: Record<string, string> = {
  operational: "Operativo", waiting: "Esperando próximos partidos", stale_data: "Datos desactualizados",
  provider_error: "Error de proveedor", scheduler_inactive: "Scheduler inactivo",
  scan_error: "Error al comprobar capturas",
};
const reasons: Record<string, string> = {
  before_deployment: "El laboratorio aún no estaba desplegado",
  no_recorded_scan: "Sin comprobación registrada durante la ventana",
  scan_failed: "Fallaron las comprobaciones registradas durante la ventana",
  unknown: "Causa sin confirmar", no_prediction: "Sin pronóstico prepartido",
  no_valid_quotes: "Sin cuota verificable", no_eligible_market: "Sin mercado elegible", captured: "Corte registrado",
};
const date = (timestamp: string | null) => timestamp ? formatMatchDateTime(timestamp, {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
}) : "Sin registro";

export default function Operations({ report }: { report: ShadowOperations }) {
  const { next_capture: next } = report;
  return (
    <section className="space-y-3 rounded-lg border border-surface-border bg-card p-4">
      <h2 className="font-semibold">Estado operativo</h2>
      <p className="text-sm text-slate-300">{states[report.status] ?? report.status} · {report.scheduler_active ? "Scheduler activo" : "Sin actividad reciente"}</p>
      <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
        {[
          ["Última comprobación correcta", date(report.last_success)],
          ["Última ingesta de cuotas con partidos asociados", date(report.last_odds_update)],
          ["Último origen de cuota disponible", date(report.latest_quote_origin)],
          ["Última consulta de cuota disponible", date(report.latest_quote_query)],
          ["Próxima ventana", next ? `${next.match} · ${date(next.window_opens)}` : "Esperando próximos partidos"],
          ["Selecciones pendientes de liquidar", report.pending_selections],
          ["Última versión en predicciones activas", report.last_prediction_model ?? "Sin registro"],
        ].map(([label, value]) => <div key={label}><dt className="text-slate-400">{label}</dt><dd>{value}</dd></div>)}
      </dl>
      {([["Próximos partidos", report.upcoming], ["Partidos capturados", report.captured], ["Capturas perdidas", report.missed]] as const).map(([label, rows]) => (
        <details key={label} className="border-t border-surface-border pt-2 text-sm">
          <summary className="cursor-pointer">{label} · {rows.length}</summary>
          <ul className="mt-2 max-h-56 space-y-1 overflow-auto text-slate-400">
            {rows.map(row => <li key={row.match_id}>{row.match} · {date(row.captured_at ?? row.kickoff)}{row.reason ? ` · ${reasons[row.reason] ?? row.reason}` : ""}{row.selections != null ? ` · ${row.selections} estrategias con selección` : ""}</li>)}
          </ul>
        </details>
      ))}
      <details className="border-t border-surface-border pt-2 text-sm">
        <summary className="cursor-pointer">Validación de temperatura · {report.calibrations.length} candidatos</summary>
        <ul className="mt-2 text-slate-400">
          {report.calibrations.length ? report.calibrations.map(row => (
            <li key={row.id}>#{row.id} · {row.dataset} · {row.reason === "accepted" ? "Temperatura aceptada en validación temporal" : row.reason === "no_temporal_improvement" ? "Sin mejora temporal" : "Muestra insuficiente"}</li>
          )) : <li>El laboratorio usa el respaldo original donde falta un calibrador 1X2 aceptado.</li>}
        </ul>
      </details>
      {!!(report.errors.length || report.provider_errors.length) && <details className="border-t border-surface-border pt-2 text-sm">
        <summary className="cursor-pointer">Errores registrados</summary>
        <ul className="mt-2 text-slate-400">
          {report.errors.map(e => <li key={e.at}>{date(e.at)} · {e.type}</li>)}
          {report.provider_errors.map(e => <li key={e.league}>{date(e.at)} · {e.provider} · {e.league}</li>)}
        </ul>
      </details>}
    </section>
  );
}
