import type { FormMatch } from "@/types";

// Fila de resultados W/D/L en forma de puntos, compacta -- compartida
// entre MatchFormSection.tsx (forma de un partido concreto) y
// HeadToHeadWidget.tsx (forma de dos equipos elegidos libremente).
const RESULT_STYLE: Record<FormMatch["result"], string> = {
  W: "bg-emerald-500/20 text-emerald-400",
  D: "bg-slate-500/20 text-slate-400",
  L: "bg-red-500/20 text-red-400",
};

export default function ResultDots({ matches }: { matches: FormMatch[] }) {
  if (matches.length === 0) {
    return <span className="text-xs text-slate-500">Sin partidos previos todavía.</span>;
  }
  // Mas reciente primero en la API -> se invierte para leer de izq a
  // derecha en orden cronologico, como un calendario.
  const chronological = [...matches].reverse();
  return (
    <div className="flex gap-1">
      {chronological.map((m) => (
        <span
          key={m.match_id}
          title={`${m.result === "W" ? "Ganó" : m.result === "L" ? "Perdió" : "Empató"} ${m.goals_for}-${m.goals_against} vs ${m.opponent}`}
          className={`flex h-6 w-6 items-center justify-center rounded text-[11px] font-semibold ${RESULT_STYLE[m.result]}`}
        >
          {m.result}
        </span>
      ))}
    </div>
  );
}
