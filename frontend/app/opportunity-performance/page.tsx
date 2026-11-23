import { getBotBankroll, getHistory } from "@/lib/api";
import BotBankrollBoard from "@/components/BotBankrollBoard";
import HistoryBoard from "@/components/HistoryBoard";
import RoundAccuracy from "@/components/RoundAccuracy";

export default async function OpportunityPerformance() {
  const [bot, trial, history] = await Promise.all([
    getBotBankroll().catch(() => null),
    getBotBankroll(true).catch(() => null),
    getHistory(undefined, 200, true).catch(() => null),
  ]);

  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Rendimiento de oportunidades</h1>
      {!!trial?.days.length && <BotBankrollBoard report={trial} trial />}
      <BotBankrollBoard report={bot} />
      <RoundAccuracy />
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Pronósticos evaluados por partido</h2>
        {history ? <HistoryBoard rows={history} simple /> : <p>No se pudo consultar el histórico de pronósticos.</p>}
      </section>
    </main>
  );
}
