import Link from "next/link";
import DashboardBoard from "@/components/DashboardBoard";
import HeadToHeadWidget from "@/components/HeadToHeadWidget";

export default function TodayPage() {
  return (
    <main className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight text-slate-100">Hoy</h1>
        <Link href="/top-signals" className="rounded-lg border border-accent/40 px-3 py-2 text-sm text-accent hover:bg-accent/10">
          Ver ranking completo
        </Link>
      </div>
      <DashboardBoard />
      <details className="rounded-lg border border-surface-border px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium">Comparar equipos</summary>
        <div className="mt-3"><HeadToHeadWidget /></div>
      </details>
    </main>
  );
}
