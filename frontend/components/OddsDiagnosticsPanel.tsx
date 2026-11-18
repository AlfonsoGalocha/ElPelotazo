"use client";

import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { getOddsDiagnostics } from "@/lib/api";
import type { OddsDiagnostics, OddsIngestionRun } from "@/lib/api";
import { formatMatchDateTime } from "@/lib/dates";

// Panel tecnico de diagnostico de cuotas (seccion "Configuracion", pedido
// real del usuario tras investigar por que la app mostraba cuotas
// desactualizadas/distintas de las reales): el ULTIMO intento real de
// cada actualizacion de cuotas (exito o fallo) y la frescura ya guardada
// en MatchOdds -- pensado para responder "por que esta cuota no se
// actualizo" sin tener que volver a ejecutar un comando a mano y mirar
// la terminal en directo. "use client" (no Server Component) para poder
// refrescarlo sin recargar toda la pagina de Configuracion.
const OPERATION_LABELS: Record<string, string> = {
  update_odds: "Cuotas (1X2/goles/BTTS)",
  update_secondary_odds: "Cuotas de corners",
};

const PROVIDER_LABELS: Record<string, string> = {
  api_football: "API-Football",
  the_odds_api: "The Odds API",
};

function RunRow({ run }: { run: OddsIngestionRun }) {
  const failed = run.error !== null;
  return (
    <tr className="border-t border-surface-border">
      <td className="px-3 py-2 text-slate-200">{run.competition_code}</td>
      <td className="px-3 py-2 text-slate-400">{OPERATION_LABELS[run.operation] ?? run.operation}</td>
      <td className="px-3 py-2 text-slate-400">{PROVIDER_LABELS[run.provider] ?? run.provider}</td>
      <td className="px-3 py-2 text-slate-400">{formatMatchDateTime(run.finished_at)}</td>
      <td className="px-3 py-2">
        {failed ? (
          <span className="rounded border border-rose-500/30 bg-rose-500/15 px-2 py-0.5 text-[10px] font-medium text-rose-400">
            ERROR
          </span>
        ) : (
          <span className="rounded border border-edge-positive/30 bg-edge-positive/10 px-2 py-0.5 text-[10px] font-medium text-edge-positive">
            OK
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-slate-400">{run.fixtures_fetched ?? "—"}</td>
      <td className="px-3 py-2 text-slate-400">{run.matched ?? "—"}</td>
      <td className="max-w-xs truncate px-3 py-2 text-xs text-rose-400" title={run.error ?? undefined}>
        {run.error ?? ""}
      </td>
    </tr>
  );
}

export default function OddsDiagnosticsPanel() {
  const [data, setData] = useState<OddsDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    setError(null);
    getOddsDiagnostics()
      .then(setData)
      .catch(() => setError("No se pudo consultar el panel de diagnostico."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="rounded-xl border border-surface-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-slate-300">
          <Activity className="h-3.5 w-3.5" />
          Diagnostico de cuotas
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1 rounded border border-surface-border px-2 py-1 text-xs text-slate-400 hover:bg-surface-alt/60 disabled:opacity-50"
        >
          <RefreshCw className={"h-3 w-3" + (loading ? " animate-spin" : "")} />
          Refrescar
        </button>
      </div>
      <p className="mb-3 text-xs text-slate-500">
        El ultimo intento REAL de refrescar cuotas por liga (exito o fallo) y la frescura ya guardada —
        pensado para responder &quot;por que esta cuota no se actualizo&quot; sin tener que mirar la terminal.
      </p>

      {error && <p className="text-sm text-red-300">{error}</p>}

      {data && !error && (
        <>
          <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-surface-border bg-surface-raised p-3">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Cuota mas reciente guardada</div>
              <div className="text-sm font-semibold text-slate-100">
                {data.most_recent_odds_recorded_at
                  ? formatMatchDateTime(data.most_recent_odds_recorded_at)
                  : "Sin cuotas guardadas todavia"}
              </div>
            </div>
            <div className="rounded-lg border border-surface-border bg-surface-raised p-3">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">
                Partidos programados con cuota
              </div>
              <div className="text-sm font-semibold text-slate-100">{data.scheduled_matches_with_odds}</div>
            </div>
            <div className="rounded-lg border border-surface-border bg-surface-raised p-3">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Casas de apuestas vistas</div>
              <div className="text-sm text-slate-100">
                {data.bookmakers_seen.length > 0 ? data.bookmakers_seen.join(", ") : "Ninguna todavia"}
              </div>
            </div>
          </div>

          {data.latest_runs.length === 0 ? (
            <p className="text-sm text-slate-500">
              Todavia no se ha ejecutado ningun <code className="rounded bg-black/40 px-1 py-0.5">update-odds</code>
              {" "}/ <code className="rounded bg-black/40 px-1 py-0.5">update-secondary-odds</code> real.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-surface-border">
              <table className="w-full text-sm">
                <thead className="bg-surface-raised text-left text-[11px] uppercase text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Competicion</th>
                    <th className="px-3 py-2">Operacion</th>
                    <th className="px-3 py-2">Fuente</th>
                    <th className="px-3 py-2">Ultimo intento</th>
                    <th className="px-3 py-2">Estado</th>
                    <th className="px-3 py-2">Fixtures</th>
                    <th className="px-3 py-2">Casaron</th>
                    <th className="px-3 py-2">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {data.latest_runs.map((run) => (
                    <RunRow key={`${run.provider}-${run.operation}-${run.competition_code}`} run={run} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
