import FeaturedMatchesBoard from "@/components/FeaturedMatchesBoard";
import HeadToHeadWidget from "@/components/HeadToHeadWidget";

// Pedido real de usuario ("quiero una app mucho mas sencillo y mas
// bonito"): el home se simplifica a dos cosas -- los partidos destacados
// de la jornada (en cajas a lo ancho, FeaturedMatchesBoard) y el buscador
// H2H. El resumen de jornada, la tarjeta de "mejor oportunidad" y el
// tablero completo por liga (HomeLeagueBoard, que podia listar TODOS los
// partidos de las 5 ligas de golpe) se retiran de aqui -- siguen
// disponibles sin perder funcionalidad en /matches (buscar/filtrar) y en
// cada /matches/[id] (desglose completo). Si se quieren de vuelta en el
// home, es un cambio trivial (un import + una linea).
export default function TodayPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 text-xl font-semibold text-slate-100">Partidos destacados</h1>
        <p className="text-sm text-slate-500">
          Las señales más claras de la jornada actual. Nunca es una certeza — entra en cualquier
          partido para el desglose completo.
        </p>
      </div>

      <FeaturedMatchesBoard />

      <HeadToHeadWidget />
    </div>
  );
}
