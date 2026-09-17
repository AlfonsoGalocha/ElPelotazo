import { getPredictionsToday } from "@/lib/api";
import type { Prediction } from "@/types";
import MatchCard from "@/components/MatchCard";

function groupByMatch(predictions: Prediction[]): Prediction[][] {
  const groups = new Map<number, Prediction[]>();
  for (const p of predictions) {
    const list = groups.get(p.match.id) ?? [];
    list.push(p);
    groups.set(p.match.id, list);
  }
  return Array.from(groups.values());
}

export default async function TodayPage() {
  let predictions: Prediction[] = [];
  let error: string | null = null;

  try {
    predictions = await getPredictionsToday();
  } catch {
    error = "No se pudo conectar con la API. Arranca el backend (uvicorn backend.app.main:app).";
  }

  const groups = groupByMatch(predictions);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Today&apos;s Matches</h1>
      <p className="mb-6 text-sm text-slate-500">
        Probabilidad estimada, probabilidad de mercado y edge estadistico para los mercados de goles
        del MVP (Over/Under, BTTS). Nunca es una certeza: revisa siempre confidence y data quality.
      </p>

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">{error}</div>
      )}

      {!error && groups.length === 0 && (
        <div className="rounded border border-surface-border bg-surface-raised p-6 text-sm text-slate-400">
          No hay predicciones generadas para hoy todavia. Ejecuta{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">python scripts/update_data.py</code>,{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">train_models.py</code> y la generacion de
          predicciones (endpoint <code className="rounded bg-black/40 px-1 py-0.5">/matches/predict</code> o el CLI{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">football-edge predict</code>).
        </div>
      )}

      <div className="flex flex-col gap-4">
        {groups.map((group) => (
          <MatchCard key={group[0].match.id} predictions={group} />
        ))}
      </div>
    </div>
  );
}
