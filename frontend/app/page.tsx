import DashboardBoard from "@/components/DashboardBoard";
import HeadToHeadWidget from "@/components/HeadToHeadWidget";

// Rediseño completo del Dashboard (pedido explicito del usuario, nov-2026):
// banner de destacados + filtros (liga/confianza) + ranking de mejores
// oportunidades como tarjetas + indicadores globales al pie, todo dentro
// de DashboardBoard (ver ese fichero). El buscador H2H se mantiene, pedido
// real del usuario en la simplificacion anterior del home -- nada se
// quita, solo se reviste visualmente.
export default function TodayPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="mb-1 text-2xl font-bold tracking-tight text-slate-100">Dashboard</h1>
        <p className="text-sm text-slate-500">
          Las señales más claras de la jornada actual, con rigor estadístico. Nunca una certeza —
          entra en cualquier partido para el desglose completo.
        </p>
      </div>

      <DashboardBoard />

      <HeadToHeadWidget />
    </div>
  );
}
