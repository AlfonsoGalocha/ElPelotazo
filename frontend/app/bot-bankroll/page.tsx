import BotBankrollBoard from "@/components/BotBankrollBoard";
import { getBotBankroll } from "@/lib/api";

export default async function BotBankrollPage() {
  const report = await getBotBankroll(true, true).catch(() => null);
  return (
    <main className="space-y-4">
      <h1 className="text-2xl font-bold">Banca del bot</h1>
      <BotBankrollBoard report={report} />
    </main>
  );
}
