import { getCompetitions, getCurrentRoundPredictions, getModelOnlyPredictions } from "@/lib/api";
import type { Prediction } from "@/types";
import BestOfDayWidget from "@/components/BestOfDayWidget";
import BestPredictionsWidget from "@/components/BestPredictionsWidget";
import HomeLeagueBoard from "@/components/HomeLeagueBoard";

function groupByMatch(predictions: Prediction[]): Prediction[][] {
  const byMatch = new Map<number, Prediction[]>();
  for (const p of predictions) {
    const list = byMatch.get(p.match.id) ?? [];
    list.push(p);
    byMatch.set(p.match.id, list);
  }
  return [...byMatch.values()].sort(
    (a, b) => new Date(a[0].match.kickoff_utc).getTime() - new Date(b[0].match.kickoff_utc).getTime()
  );
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
          Partidos pendientes de la jornada en curso de cada liga (no simplemente "los próximos
          partidos que haya"): mientras queden partidos sin jugar de una jornada, esa sigue siendo la
          jornada actual aunque la siguiente ya tenga fecha. Probabilidad estimada, probabilidad de
          mercado (consenso de varias casas de apuestas cuando hay cuotas reales disponibles) y edge
          estadístico. Nunca es una certeza: revisa siempre la confianza y la calidad de los datos.
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
