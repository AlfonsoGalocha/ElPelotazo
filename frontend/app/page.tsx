import { getUpcomingPredictions } from "@/lib/api";
import type { Prediction } from "@/types";
import MatchCard from "@/components/MatchCard";
import BestPredictionsWidget from "@/components/BestPredictionsWidget";

function groupByDateThenMatch(predictions: Prediction[]): Map<string, Prediction[][]> {
  const byDate = new Map<string, Map<number, Prediction[]>>();
  for (const p of predictions) {
    const dateKey = p.match.kickoff_utc.slice(0, 10);
    const byMatch = byDate.get(dateKey) ?? new Map<number, Prediction[]>();
    const list = byMatch.get(p.match.id) ?? [];
    list.push(p);
    byMatch.set(p.match.id, list);
    byDate.set(dateKey, byMatch);
  }
  const result = new Map<string, Prediction[][]>();
  for (const [date, byMatch] of [...byDate.entries()].sort()) {
    result.set(date, [...byMatch.values()]);
  }
  return result;
}

function formatDateHeading(dateKey: string): string {
  const date = new Date(`${dateKey}T00:00:00`);
  const today = new Date();
  const isToday = date.toDateString() === today.toDateString();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const isTomorrow = date.toDateString() === tomorrow.toDateString();

  const formatted = date.toLocaleDateString("es-ES", {
    weekday: "long",
    day: "2-digit",
    month: "long",
  });
  if (isToday) return `Hoy — ${formatted}`;
  if (isTomorrow) return `Mañana — ${formatted}`;
  return formatted;
}

export default async function TodayPage() {
  let predictions: Prediction[] = [];
  let error: string | null = null;

  try {
    predictions = await getUpcomingPredictions(7);
  } catch {
    error = "No se pudo conectar con la API. Arranca el backend (uvicorn backend.app.main:app).";
  }

  const grouped = groupByDateThenMatch(predictions);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Próximos partidos</h1>
        <p className="mb-4 text-sm text-slate-500">
          Partidos programados en los próximos 7 días. Probabilidad estimada, probabilidad de mercado
          (cuando hay cuotas reales disponibles) y edge estadístico. Nunca es una certeza: revisa siempre
          la confianza y la calidad de los datos.
        </p>
      </div>

      <BestPredictionsWidget />

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">{error}</div>
      )}

      {!error && grouped.size === 0 && (
        <div className="rounded border border-surface-border bg-surface-raised p-6 text-sm text-slate-400">
          No hay partidos programados en los próximos 7 días todavía. Ejecuta{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">football-edge refresh</code> (o{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">python scripts/refresh_all.py</code>) para
          descargar datos, entrenar los modelos y generar las predicciones en un solo paso.
        </div>
      )}

      {[...grouped.entries()].map(([dateKey, matches]) => (
        <div key={dateKey}>
          <h2 className="mb-3 text-sm font-semibold capitalize text-slate-400">{formatDateHeading(dateKey)}</h2>
          <div className="flex flex-col gap-4">
            {matches.map((group) => (
              <MatchCard key={group[0].match.id} predictions={group} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
