import { getCompetitions, getCurrentRoundPredictions, getModelOnlyPredictions } from "@/lib/api";
import type { Prediction } from "@/types";
import BestOfDayWidget from "@/components/BestOfDayWidget";
import BestPredictionsWidget from "@/components/BestPredictionsWidget";
import HomeLeagueBoard from "@/components/HomeLeagueBoard";

// Pedido explicito del usuario ("quiero mas orden... que me ponga las
// mejores predicciones"): dentro de la jornada, los partidos con una
// señal mas fuerte (mayor edge de su mejor prediccion) van primero -- la
// hora de inicio sigue visible en cada tarjeta, solo deja de ser el orden
// principal. Un partido sin ninguna prediccion con edge valido todavia
// (p.ej. sin mercado real aun) cae al final, ordenado por fecha entre si.
function bestEdge(predictions: Prediction[]): number {
  const best = predictions.find((p) => p.is_best_prediction);
  return best?.edge ?? -Infinity;
}

function groupByMatch(predictions: Prediction[]): Prediction[][] {
  const byMatch = new Map<number, Prediction[]>();
  for (const p of predictions) {
    const list = byMatch.get(p.match.id) ?? [];
    list.push(p);
    byMatch.set(p.match.id, list);
  }
  return [...byMatch.values()].sort((a, b) => {
    const edgeDiff = bestEdge(b) - bestEdge(a);
    if (edgeDiff !== 0) return edgeDiff;
    return new Date(a[0].match.kickoff_utc).getTime() - new Date(b[0].match.kickoff_utc).getTime();
  });
}

function formatRange(start: string | null, end: string | null): string {
  if (!start || !end) return "";
  const startDate = new Date(start);
  const endDate = new Date(end);
  const fmt = (d: Date) => d.toLocaleDateString("es-ES", { day: "2-digit", month: "short" });
  return startDate.toDateString() === endDate.toDateString() ? fmt(startDate) : `${fmt(startDate)} - ${fmt(endDate)}`;
}

export default async function TodayPage() {
  let error: string | null = null;
  let competitionBlocks: {
    code: string;
    name: string;
    roundLabel: string;
    isFallback: boolean;
    isNextRound: boolean;
    dateRange: string;
    matches: Prediction[][];
  }[] = [];

  try {
    const competitions = await getCompetitions();
    const results = await Promise.all(
      competitions.map(async (competition) => {
        const data = await getCurrentRoundPredictions(competition.code);
        return { competition, data };
      })
    );
    competitionBlocks = results
      .filter((r) => r.data.round !== null && r.data.predictions.length > 0)
      .map(({ competition, data }) => ({
        code: competition.code,
        name: competition.name,
        roundLabel:
          data.round!.round !== null ? `Jornada ${data.round!.round}` : "Próximos partidos",
        isFallback: data.round!.is_fallback,
        isNextRound: data.round!.is_next_round,
        dateRange: formatRange(data.round!.round_start, data.round!.round_end),
        matches: groupByMatch(data.predictions),
      }));
  } catch {
    error = "No se pudo conectar con la API. Arranca el backend (uvicorn backend.app.main:app).";
  }

  const modelOnly = error ? [] : await getModelOnlyPredictions(20).catch(() => []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Jornada actual</h1>
        <p className="mb-4 text-sm text-slate-500">
          Lo que dice el modelo sobre los partidos que quedan por jugar de la jornada en curso de
          cada liga. Nunca es una certeza — toca "Ver todos los mercados" en cualquier partido si
          quieres el desglose completo.
        </p>
      </div>

      <BestOfDayWidget />
      <BestPredictionsWidget />

      {error && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">{error}</div>
      )}

      {!error && competitionBlocks.length === 0 && (
        <div className="rounded border border-surface-border bg-surface-raised p-6 text-sm text-slate-400">
          No hay partidos programados todavía. Ejecuta{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">football-edge refresh</code> (o{" "}
          <code className="rounded bg-black/40 px-1 py-0.5">python scripts/refresh_all.py</code>) para
          descargar datos, entrenar los modelos y generar las predicciones en un solo paso.
        </div>
      )}

      {!error && competitionBlocks.length > 0 && (
        <HomeLeagueBoard competitionBlocks={competitionBlocks} modelOnly={modelOnly} />
      )}
    </div>
  );
}
