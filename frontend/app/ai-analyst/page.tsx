import JarvisChat from "@/components/JarvisChat";

// Reposicionamiento pedido: mismo motor (backend/app/agent/, herramientas
// reales, nunca inventa datos), presentado con tono analitico en vez de
// "asistente personal". Las "preguntas rapidas" y el embebido en el
// detalle de partido llegan en una pasada dedicada (modulo Analista IA).
export default function AiAnalystPage() {
  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Football AI Analyst</h1>
      <p className="mb-4 text-sm text-slate-500">
        Analista conversacional sobre las 5 grandes ligas europeas, apoyado SOLO en datos reales
        del sistema (nunca inventa partidos, probabilidades, cuotas ni lesiones) para responder —
        explica predicciones, compara mercados, identifica riesgos y resume la forma reciente de
        los equipos.
      </p>
      <JarvisChat />
    </div>
  );
}
