import BotPlanBoard from "@/components/BotPlanBoard";
import BotTabs from "@/components/BotTabs";

export default function BotPlanPage() {
  return (
    <main className="space-y-4">
      <h1 className="text-2xl font-bold">Banca del bot</h1>
      <BotTabs />
      <BotPlanBoard />
    </main>
  );
}
