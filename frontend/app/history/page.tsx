import { History } from "lucide-react";
import { getHistory } from "@/lib/api";
import HistoryBoard from "@/components/HistoryBoard";
import { EmptyState } from "@/components/EmptyState";

export default async function HistoryPage() {
  // Limite alto (y no filtrado por liga server-side): pedido real del
  // usuario, "quiero que me salga la ultima jornada de cada liga" -- se
  // trae de sobra para las 5 ligas y HistoryBoard agrupa/filtra en el
  // cliente, para poder cambiar de liga sin otra ida y vuelta al backend.
  const history = await getHistory(undefined, 150).catch(() => []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-100">
          <History className="h-5 w-5 text-accent" />
          Histórico
        </h1>
        <p className="text-sm text-slate-500">
          La última jornada ya jugada de cada liga, con su mejor predicción (mayor puntuación de
          señal entre las válidas) comparada contra el resultado real. Nada aquí se recalcula con
          criterios de hoy: es exactamente lo que el sistema predijo antes del pitido inicial. Pincha
          un partido para ver el desglose completo y si acertó o falló.
        </p>
      </div>

      {history.length === 0 && (
        <EmptyState
          title="Sin partidos finalizados todavía"
          description="Ejecuta football-edge refresh cuando haya resultados reales nuevos."
        />
      )}

      {history.length > 0 && <HistoryBoard rows={history} />}
    </div>
  );
}
