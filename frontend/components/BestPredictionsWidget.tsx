import Link from "next/link";
import { getBestPredictions } from "@/lib/api";
import MatchGridCard from "./MatchGridCard";

export default async function BestPredictionsWidget() {
  const predictions = await getBestPredictions(5).catch(() => []);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Las 5 mejores predicciones
      </div>
      <p className="mb-3 text-xs text-slate-500">
        De los próximos 4 días, solo predicciones con mercado real (cuota, probabilidad de mercado y
        edge válidos —{" "}
        <Link href="/#sin-mercado" className="underline hover:text-slate-300">
          ver aparte
        </Link>{" "}
        las que no tienen mercado todavía). Se prioriza probabilidad alta del modelo{" "}
        <span className="text-slate-400">junto con</span> una cuota mejor que la justa (edge real):
        una probabilidad altísima a una cuota irrisoria (ej. 98% a 1.02) no puntúa alto, y un edge
        grande en un resultado muy improbable (cuota &gt; 6.0) directamente no entra.
      </p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {predictions.map((p) => (
          <MatchGridCard key={p.id} predictions={[p]} />
        ))}
        {predictions.length === 0 && (
          <div className="col-span-full text-sm text-slate-500">
            Sin señales con mercado válido todavía. Ejecuta{" "}
            <code className="rounded bg-black/40 px-1 py-0.5">football-edge update-odds</code>.
          </div>
        )}
      </div>
    </div>
  );
}
