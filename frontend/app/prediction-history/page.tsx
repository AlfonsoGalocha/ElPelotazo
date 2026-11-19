import { getPublications } from "@/lib/api";
import { PublicationRows } from "@/components/PublicationBoard";

export default async function PredictionHistory({ searchParams }: { searchParams: Record<string, string | undefined> }) {
  const report = await getPublications(searchParams).catch(() => null);
  const fields = [["competition","Liga"],["matchday","Jornada"],["market","Mercado"],["model_version","Versión"],
    ["min_probability","Probabilidad mínima (0–1)"],["max_probability","Probabilidad máxima (0–1)"],["outcome","Estado (won, lost, void)"]];
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Histórico de predicciones</h1>
      <p className="text-sm text-slate-400">Pronósticos registrados en el corte prepartido desde la activación de las capturas.</p>
      <form className="flex flex-wrap items-end gap-2">
        {fields.map(([key, title]) => <label key={key} className="grid gap-1 text-xs text-slate-400">
          {title}<input className="rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-100"
            name={key} defaultValue={searchParams[key]} />
        </label>)}
        <button className="rounded border border-surface-border px-3 py-1">Filtrar</button>
      </form>
      {report ? <PublicationRows rows={report.history} /> : <p>No se pudo consultar el histórico.</p>}
    </main>
  );
}
