import HistoryBoard from "@/components/HistoryBoard";
import { getHistory } from "@/lib/api";

export default async function HistoryPage() {
  const history = await getHistory(undefined, 200, true).catch(() => null);
  return (
    <main className="space-y-3">
      <h1 className="text-2xl font-bold">Histórico por jornada</h1>
      {history ? <HistoryBoard rows={history} simple /> : <p>No se pudo consultar el histórico de pronósticos.</p>}
    </main>
  );
}
