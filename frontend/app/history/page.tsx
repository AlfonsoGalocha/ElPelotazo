import { redirect } from "next/navigation";

// "Histórico de jornadas" se fusionó con "Estadísticas" en una única
// sección "Rendimiento del modelo" (pedido real del usuario) -- esta
// ruta se conserva solo como redirección para no romper enlaces
// antiguos (favoritos, historial del navegador), nunca como página real.
// El detalle de un partido concreto (/history/[id]) NO se mueve: sigue
// viviendo aquí, enlazado desde la pestaña "Jornadas" de /model.
export default function HistoryRedirectPage() {
  redirect("/model?tab=jornadas");
}
