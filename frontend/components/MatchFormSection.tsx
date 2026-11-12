import type { FormMatch, MatchForm } from "@/types";

// Pedido real del usuario: "un apartado tipo h2h de como viene ese
// equipo, que suele hacer dentro/fuera de casa" -- forma reciente (en
// general y especifica en su rol local/visitante de este partido) + cara
// a cara historico. Solo partidos REALMENTE 'finished' (nunca inventado),
// y si un equipo no tiene historial suficiente todavia, se dice asi en
// vez de forzar una fila vacia.

const RESULT_STYLE: Record<FormMatch["result"], string> = {
  W: "bg-emerald-500/20 text-emerald-400",
  D: "bg-slate-500/20 text-slate-400",
  L: "bg-red-500/20 text-red-400",
};

function ResultDots({ matches }: { matches: FormMatch[] }) {
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

export default function MatchFormSection({ form }: { form: MatchForm }) {
  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Cómo vienen los equipos
      </h2>

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-sm font-medium text-slate-200">{form.home_team.team.canonical_name}</div>
          <div className="mb-2 text-xs text-slate-500">Últimos partidos (cualquier rival)</div>
          <ResultDots matches={form.home_team.recent} />
          <div className="mb-2 mt-3 text-xs text-slate-500">Últimos como local</div>
          <ResultDots matches={form.home_team.recent_at_venue} />
        </div>
        <div>
          <div className="mb-1 text-sm font-medium text-slate-200">{form.away_team.team.canonical_name}</div>
          <div className="mb-2 text-xs text-slate-500">Últimos partidos (cualquier rival)</div>
          <ResultDots matches={form.away_team.recent} />
          <div className="mb-2 mt-3 text-xs text-slate-500">Últimos como visitante</div>
          <ResultDots matches={form.away_team.recent_at_venue} />
        </div>
      </div>

      <div className="mt-5 border-t border-surface-border pt-4">
        <div className="mb-2 text-xs text-slate-500">
          Cara a cara ({form.home_team.team.canonical_name} vs {form.away_team.team.canonical_name})
        </div>
        {form.head_to_head.length === 0 ? (
          <span className="text-xs text-slate-500">Sin enfrentamientos previos registrados.</span>
        ) : (
          <div className="flex flex-col gap-1">
            {form.head_to_head.map((m) => (
              <div key={m.match_id} className="flex items-center gap-2 text-xs text-slate-400">
                <span className="w-20 shrink-0">
                  {new Date(m.kickoff_utc).toLocaleDateString("es-ES", { day: "2-digit", month: "short", year: "numeric" })}
                </span>
                <span>
                  {m.venue === "home" ? form.home_team.team.canonical_name : form.away_team.team.canonical_name}{" "}
                  <span className="text-slate-200">
                    {m.goals_for}-{m.goals_against}
                  </span>{" "}
                  {m.opponent}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
