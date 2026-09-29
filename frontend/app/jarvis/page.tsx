import JarvisChat from "@/components/JarvisChat";

export default function JarvisPage() {
  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-slate-100">Jarvis</h1>
      <p className="mb-4 text-sm text-slate-500">
        Asistente conversacional especializado en las 5 grandes ligas europeas. Consulta
        herramientas reales del sistema (nunca inventa datos ni presenta una predicción como una
        certeza) para responder. De momento solo sabe qué partidos hay en la jornada actual —
        se irán añadiendo más capacidades.
      </p>
      <JarvisChat />
    </div>
  );
}
