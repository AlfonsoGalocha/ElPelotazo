import { Settings, Database } from "lucide-react";
import { getDataSourcesStatus, getRankingConfig } from "@/lib/api";
import { ErrorState } from "@/components/EmptyState";
import RankingConfigForm from "@/components/RankingConfigForm";
import OddsDiagnosticsPanel from "@/components/OddsDiagnosticsPanel";

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
// verdad: esta app no tiene cuentas de usuario, asi que las demas
// secciones son de solo lectura (fuentes de datos, configuradas via
// variables de entorno del backend). El filtro de cuotas de "Mejores
// oportunidades" (abajo) es la EXCEPCION real: una preferencia que si se
// guarda de verdad (RankingConfig, compartida por ahora -- no hay
// cuentas de usuario todavia, asi que no es "por persona").
export default async function SettingsPage() {
  let status: Awaited<ReturnType<typeof getDataSourcesStatus>> | null = null;
  let rankingConfig: Awaited<ReturnType<typeof getRankingConfig>> | null = null;
  let error: string | null = null;
  try {
    [status, rankingConfig] = await Promise.all([getDataSourcesStatus(), getRankingConfig()]);
  } catch {
    error = "No se pudo conectar con la API.";
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
          <Settings className="h-5 w-5 text-accent" />
          Configuración
        </h1>
        <p className="text-sm text-slate-500">
          Esta aplicación todavía no tiene cuentas de usuario — el filtro de cuotas de abajo es
          compartido (no &quot;por persona&quot;), pero sí se guarda de verdad. El resto es de solo
          lectura: el estado real de las fuentes de datos configuradas en el backend.
        </p>
      </div>

      {error && <ErrorState message={error} />}

      {rankingConfig && <RankingConfigForm initial={rankingConfig} />}

      {status && (
        <div className="rounded-xl border border-surface-border bg-card p-4">
          <div className="mb-3 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-slate-300">
            <Database className="h-3.5 w-3.5" />
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

      <OddsDiagnosticsPanel />

      <div className="rounded-xl border border-dashed border-surface-border p-4 text-sm text-slate-500">
        Pendiente de construir (requiere cuentas de usuario, que no existen hoy): preferencias
        guardadas POR PERSONA de liga favorita, tema, notificaciones.
      </div>
    </div>
  );
}
