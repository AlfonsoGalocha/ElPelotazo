import Link from "next/link";
import { getPublication } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";

export default async function CapturedPrediction({ params }: { params: { id: string } }) {
  const publication = await getPublication(Number(params.id)).catch(() => null);
  if (!publication) return <p>No se pudo consultar la captura oficial de este partido.</p>;
  return (
    <main className="space-y-5">
      <h1 className="text-2xl font-bold">Captura prepartido</h1>
      <p className="text-sm text-slate-400">
        {publication.captured_at.replace("T", " ").slice(0, 16)} UTC · Ranking {publication.ranking_version}
      </p>
      {Object.entries(publication.groups).map(([family, group]) => (
        <section key={family} className="rounded-xl border border-surface-border p-4">
          <h2 className="font-semibold">{family}</h2>
          <ul className="mt-2 text-sm text-slate-300">
            {group.alternatives.map(p => <li key={p.market}>
              {MARKET_DISPLAY_NAMES[p.market] ?? p.market} {p.market === group.principal ? "· Principal" : ""}
              {" · "}{(100 * p.probability).toFixed(1)} % · Calibrada {p.calibrated == null ? "—" : `${(100*p.calibrated).toFixed(1)} %`}
              {" · Cuota "}{p.odds?.toFixed(2) ?? "—"} · Modelo {p.model_version_id}
            </li>)}
          </ul>
        </section>
      ))}
      <p className="text-sm">
        {publication.recommendation ? `Recomendación oficial · ${MARKET_DISPLAY_NAMES[publication.recommendation.market] ?? publication.recommendation.market}` : "El corte oficial no registró una recomendación de valor."}
      </p>
      {publication.feature_snapshots.length ? publication.feature_snapshots.map(snapshot => (
        <details key={snapshot.id} className="rounded border border-surface-border p-3">
          <summary className="cursor-pointer">Variables originales del equipo {snapshot.is_home ? "local" : "visitante"}</summary>
          <pre className="mt-2 overflow-x-auto text-xs">{JSON.stringify(snapshot.features, null, 2)}</pre>
        </details>
      )) : <p className="text-sm text-slate-400">La captura no tiene variables enlazadas.</p>}
      <Link href={`/history/${params.id}`} className="text-sm text-accent underline">Consultar resultado y liquidación</Link>
    </main>
  );
}
