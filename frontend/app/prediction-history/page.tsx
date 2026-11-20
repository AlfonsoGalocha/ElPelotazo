import Link from "next/link";
import HistoryBoard from "@/components/HistoryBoard";
import { getHistory, getPublications } from "@/lib/api";

export default async function PredictionHistory() {
  const [history, publications] = await Promise.all([
    getHistory(undefined, 200, true).catch(() => null),
    getPublications().catch(() => null),
  ]);
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Histórico de predicciones</h1>
      <p className="text-sm text-slate-400">Elige una liga y una jornada para ver los partidos y sus predicciones guardadas.</p>
      {history ? <HistoryBoard rows={history} simple /> : <p>No se pudo consultar el histórico.</p>}
      {!!publications?.history.length && <details className="rounded-xl border border-surface-border p-4">
        <summary className="cursor-pointer">Capturas prepartido registradas</summary>
        {Array.from(new Map(publications.history.map(row => [row.match_id, row.match])).entries()).map(([id, match]) => (
          <Link className="mt-2 block text-sm text-accent underline" key={id} href={`/prediction-history/${id}`}>{match}</Link>
        ))}
      </details>}
    </main>
  );
}
