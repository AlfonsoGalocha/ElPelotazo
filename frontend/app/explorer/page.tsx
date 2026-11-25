import ExplorerBoard from "@/components/analysis/ExplorerBoard";

export default function ExplorerPage() {
  return (
    <main className="space-y-3">
      <div>
        <h1 className="text-2xl font-bold">Explorador estadístico</h1>
        <p className="text-sm text-slate-400">Descubre partidos por tendencias históricas o por probabilidad del modelo, sin abrirlos uno a uno.</p>
      </div>
      <ExplorerBoard />
    </main>
  );
}
