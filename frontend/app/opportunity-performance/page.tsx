import Link from "next/link";
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
      <h1 className="text-2xl font-bold">Rendimiento</h1>
      <nav aria-label="Secciones de rendimiento" className="flex flex-wrap gap-4 text-sm text-accent">
        <a href="#acierto">Acierto y simulación</a>
        <a href="#historico">Histórico por jornada</a>
        <a href="#banca">Banca del bot</a>
        <Link href="/opportunity-performance/strategies">Laboratorio de estrategias</Link>
      </nav>
      <div id="acierto" className="scroll-mt-20"><RoundAccuracy /></div>
      <section id="historico" className="scroll-mt-20 space-y-3">
        <h2 className="text-lg font-semibold">Pronósticos evaluados por partido</h2>
        {history ? <HistoryBoard rows={history} simple /> : <p>No se pudo consultar el histórico de pronósticos.</p>}
      </section>
      <div id="banca" className="scroll-mt-20 space-y-4">
        <BotBankrollBoard report={bot} />
        {!!trial?.days.length && <BotBankrollBoard report={trial} trial />}
      </div>
    </main>
  );
}
