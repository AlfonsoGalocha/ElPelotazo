import { getBestPredictions } from "@/lib/api";
import FeaturedMatchBox from "./FeaturedMatchBox";

// Pedido real de usuario ("quiero una app mucho mas sencillo... partidos
// destacados de la jornada... en cajas a lo ancho... no quiero una lista
// infinita"): un numero FIJO de partidos (nunca paginacion/"ver mas"), en
// cajas apiladas a lo ancho en vez del grid de cuadraditos anterior. Misma
// fuente real que antes (/predictions/best: solo predicciones con mercado
// real y filtro de calidad, nunca una señal inventada para rellenar).
const FEATURED_LIMIT = 8;

export default async function FeaturedMatchesBoard() {
  let predictions: Awaited<ReturnType<typeof getBestPredictions>> = [];
  let connectionError = false;
  try {
    predictions = await getBestPredictions(FEATURED_LIMIT);
  } catch {
    connectionError = true;
  }

  if (connectionError) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6 text-sm text-red-300">
        No se pudo conectar con la API. Arranca el backend (uvicorn backend.app.main:app).
      </div>
    );
  }

  if (predictions.length === 0) {
    return (
      <div className="rounded-lg border border-surface-border bg-surface-raised p-6 text-sm text-slate-500">
        Sin señales con mercado válido todavía. Ejecuta{" "}
        <code className="rounded bg-black/40 px-1 py-0.5">football-edge update-odds</code>.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {predictions.map((p) => (
        <FeaturedMatchBox key={p.id} prediction={p} />
      ))}
    </div>
  );
}
