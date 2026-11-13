import { getDataSourcesStatus } from "@/lib/api";

function StatusDot({ on }: { on: boolean }) {
  return (
    <span
      className={
        "inline-block h-2 w-2 rounded-full " + (on ? "bg-edge-positive" : "bg-slate-600")
      }
    />
  );
}

// Pagina "Configuracion" del nuevo menu. Honesto sobre lo que hay de
// verdad: esta app no tiene cuentas de usuario ni un sistema de
// preferencias persistentes todavia -- no se simula un panel de ajustes
// que en realidad no guarda nada. Lo unico real que se puede mostrar hoy
// es el ESTADO de las fuentes de datos (configuradas via variables de
// entorno del backend, nunca desde aqui).
export default async function SettingsPage() {
  let status: Awaited<ReturnType<typeof getDataSourcesStatus>> | null = null;
  let error: string | null = null;
  try {
    status = await getDataSourcesStatus();
  } catch {
    error = "No se pudo conectar con la API.";
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Configuración</h1>
        <p className="text-sm text-slate-500">
          Esta aplicación todavía no tiene cuentas de usuario ni preferencias que se guarden por
          persona — lo que sigue es de solo lectura: el estado real de las fuentes de datos
          configuradas en el backend.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">{error}</div>
      )}

      {status && (
        <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
          <div className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-300">
            Fuentes de datos
          </div>
          <div className="flex flex-col gap-2 text-sm">
            <div className="flex items-center gap-2">
              <StatusDot on={status.football_data_co_uk_enabled} />
              <span className="text-slate-200">Histórico (football-data.co.uk / mirror)</span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot on={status.api_football_enabled} />
              <span className="text-slate-200">
                Fixtures y cuotas en vivo — API-Football{" "}
                {!status.api_football_enabled && <span className="text-slate-500">(sin configurar)</span>}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot on={status.odds_api_enabled} />
              <span className="text-slate-200">
                Cuotas — The Odds API{" "}
                {!status.odds_api_enabled && <span className="text-slate-500">(sin configurar)</span>}
              </span>
            </div>
          </div>
          <p className="mt-4 text-xs text-slate-500">
            Para cambiar esto hace falta editar el <code className="rounded bg-black/40 px-1 py-0.5">.env</code> del
            backend (claves/flags de <code className="rounded bg-black/40 px-1 py-0.5">Settings</code>) y reiniciar —
            no hay manera de hacerlo desde aquí todavía.
          </p>
        </div>
      )}

      <div className="rounded-lg border border-dashed border-surface-border p-4 text-sm text-slate-500">
        Pendiente de construir (requiere cuentas de usuario, que no existen hoy): preferencias
        guardadas de liga favorita, tema, notificaciones.
      </div>
    </div>
  );
}
