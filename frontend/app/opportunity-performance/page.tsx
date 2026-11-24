import Link from "next/link";
import RoundAccuracy from "@/components/RoundAccuracy";

export default function OpportunityPerformance() {
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Rendimiento</h1>
      <nav className="flex flex-wrap gap-4 text-sm text-accent" aria-label="Rendimiento">
        <Link href="/opportunity-performance/strategies">Laboratorio de estrategias</Link>
        <Link href="/opportunity-performance/learning">Aprendizaje del modelo</Link>
      </nav>
      <div id="acierto" className="scroll-mt-20"><RoundAccuracy filters /></div>
    </main>
  );
}
