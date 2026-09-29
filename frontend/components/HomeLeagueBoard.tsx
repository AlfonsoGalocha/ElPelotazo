"use client";

import { useState } from "react";
import Link from "next/link";
import type { Prediction } from "@/types";
import MatchCard from "@/components/MatchCard";
import ModelOnlySection from "@/components/ModelOnlySection";

interface CompetitionBlock {
  code: string;
  name: string;
  roundLabel: string;
  isFallback: boolean;
  dateRange: string;
  matches: Prediction[][];
}

// Filtro por liga (pedido por el usuario): los datos de TODAS las ligas ya
// se traen server-side (page.tsx) -- aqui solo se decide cuales mostrar,
// sin volver a pedir nada a la API. Por defecto se muestran todas.
export default function HomeLeagueBoard({
  competitionBlocks,
  modelOnly,
}: {
  competitionBlocks: CompetitionBlock[];
  modelOnly: Prediction[];
}) {
  const [selected, setSelected] = useState<string[]>(() => competitionBlocks.map((b) => b.code));

  function toggle(code: string) {
    setSelected((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));
  }

  const allSelected = selected.length === competitionBlocks.length;
  const visibleBlocks = competitionBlocks.filter((b) => selected.includes(b.code));
  const visibleModelOnly = modelOnly.filter((p) => selected.includes(p.match.competition.code));

  return (
    <>
      {competitionBlocks.length > 1 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Ligas:</span>
          <button
            onClick={() => setSelected(competitionBlocks.map((b) => b.code))}
            className={
              "rounded-full border px-3 py-1 " +
              (allSelected
                ? "border-slate-400 bg-slate-700 text-slate-100"
                : "border-surface-border text-slate-400 hover:bg-surface-raised")
            }
          >
            Todas
          </button>
          {competitionBlocks.map((b) => (
            <button
              key={b.code}
              onClick={() => toggle(b.code)}
              className={
                "rounded-full border px-3 py-1 " +
                (selected.includes(b.code)
                  ? "border-slate-400 bg-slate-700 text-slate-100"
                  : "border-surface-border text-slate-500 hover:bg-surface-raised")
              }
            >
              {b.name}
            </button>
          ))}
        </div>
      )}

      {visibleBlocks.length === 0 && (
        <div className="rounded border border-surface-border bg-surface-raised p-6 text-sm text-slate-400">
          No hay ninguna liga seleccionada.
        </div>
      )}

      {visibleBlocks.map((block) => (
        <div key={block.code}>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold text-slate-400">
              <span className="uppercase tracking-wide">{block.roundLabel}</span>
              <span className="mx-2 text-slate-600">·</span>
              <span>{block.name}</span>
              {block.dateRange && (
                <>
                  <span className="mx-2 text-slate-600">·</span>
                  <span>{block.dateRange}</span>
                </>
              )}
            </h2>
            <Link href="/top-signals" className="text-xs text-slate-500 hover:text-slate-300">
              Ver mejores señales →
            </Link>
          </div>
          {block.isFallback && (
            <p className="mb-3 text-xs text-amber-500/80">
              Esta liga aún no tiene datos de jornada real para sus próximos partidos (fixtures
              pendientes de refrescar): se muestra por fecha en su lugar.
            </p>
          )}
          <div className="flex flex-col gap-4">
            {block.matches.map((group) => (
              <MatchCard key={group[0].match.id} predictions={group} />
            ))}
          </div>
        </div>
      ))}

      {visibleModelOnly.length > 0 && <ModelOnlySection predictions={visibleModelOnly} />}
    </>
  );
}
