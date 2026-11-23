import HistoryBoard from "@/components/HistoryBoard";
import { getHistory } from "@/lib/api";

export default async function PredictionHistory() {
  const history = await getHistory(undefined, 200, true).catch(() => null);
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Histórico de predicciones</h1>
      <p className="text-sm text-slate-400">Elige una liga y una jornada para ver los partidos y sus predicciones guardadas.</p>
      {history ? <HistoryBoard rows={history} simple /> : <p>No se pudo consultar el histórico.</p>}
    </main>
  );
}
