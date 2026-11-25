import BotBankrollBoard from "@/components/BotBankrollBoard";
import BotTabs from "@/components/BotTabs";
import { getBotBankroll, getBotSimulations } from "@/lib/api";

export default async function BotBankrollPage() {
  const [report, simulations] = await Promise.all([
    getBotBankroll(true, true).catch(() => null),
    getBotSimulations().catch(() => null),
  ]);
  return (
    <main className="space-y-4">
      <h1 className="text-2xl font-bold">Banca del bot</h1>
      <BotTabs />
      <BotBankrollBoard report={report} simulations={simulations} />
    </main>
  );
}
