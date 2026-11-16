"use client";

import { useEffect, useState } from "react";
import { Sparkles, SlidersHorizontal } from "lucide-react";
import { getCompetitions, getSummary, getTopSignals } from "@/lib/api";
import type { Competition, Prediction, Summary } from "@/types";
import MatchCard from "./MatchCard";
import { MatchCardSkeletonGrid } from "./Skeleton";
import { EmptyState, ErrorState } from "./EmptyState";
import StatsFooter from "./StatsFooter";

type QualityFilter = "ALL" | "HIGH" | "MEDIUM" | "LOW";

const QUALITY_LABELS: Record<QualityFilter, string> = {
  ALL: "Todas",
  HIGH: "Alta confianza",
  MEDIUM: "Media confianza",
  LOW: "Baja confianza",
};

// Dashboard del rediseño: banner de destacados (las 3 de mayor score),
// filtros (competicion/confianza), y el resto de la jornada actual como
// ranking de tarjetas -- UNA sola llamada a /predictions/top-signals
// (nunca dos) de la que se derivan tanto el banner como la rejilla
// completa, para no duplicar peticiones.
export default function DashboardBoard() {
  const [competitions, setCompetitions] = useState<Competition[]>([]);
  const [competitionCode, setCompetitionCode] = useState<string | null>(null);
  const [quality, setQuality] = useState<QualityFilter>("ALL");
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompetitions()
      .then(setCompetitions)
      .catch(() => setCompetitions([]));
    getSummary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    const timeout = setTimeout(() => {
      getTopSignals({
        limit: 24,
        scope: "current_round",
        competitionCode: competitionCode ?? undefined,
        quality,
      })
        .then(setPredictions)
        .catch(() => setError("Arranca el backend (uvicorn backend.app.main:app) o revisa NEXT_PUBLIC_API_URL."))
        .finally(() => setLoading(false));
    }, 150);
    return () => clearTimeout(timeout);
  }, [competitionCode, quality]);

  const featured = predictions.slice(0, 3);

  return (
    <div className="flex flex-col gap-6">
      {!loading && !error && featured.length > 0 && (
        <section>
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-200">
            <Sparkles className="h-4 w-4 text-opportunity" />
            Destacados de la jornada
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {featured.map((p) => (
              <MatchCard key={`featured-${p.id}`} prediction={p} />
            ))}
          </div>
        </section>
      )}

      <section className="flex flex-wrap items-center gap-2 rounded-xl border border-surface-border bg-card p-3">
        <SlidersHorizontal className="h-4 w-4 shrink-0 text-slate-500" />
        <span className="text-xs text-slate-500">Liga:</span>
        {competitions.map((c) => (
          <button
            key={c.code}
            onClick={() => setCompetitionCode((prev) => (prev === c.code ? null : c.code))}
            className={
              "rounded-full border px-3 py-1 text-xs transition-colors " +
              (competitionCode === c.code
                ? "border-accent/50 bg-accent/15 text-slate-100"
                : "border-surface-border text-slate-400 hover:bg-surface-alt/60")
            }
          >
            {c.name}
          </button>
        ))}
        <span className="ml-2 text-xs text-slate-500">Confianza:</span>
        {(Object.keys(QUALITY_LABELS) as QualityFilter[]).map((key) => (
          <button
            key={key}
            onClick={() => setQuality(key)}
            className={
              "rounded-full border px-3 py-1 text-xs transition-colors " +
              (quality === key
                ? "border-accent/50 bg-accent/15 text-slate-100"
                : "border-surface-border text-slate-400 hover:bg-surface-alt/60")
            }
          >
            {QUALITY_LABELS[key]}
          </button>
        ))}
      </section>

      <section>
        <div className="mb-3 text-sm font-semibold text-slate-200">Mejores oportunidades</div>
        {error && <ErrorState message={error} />}
        {!error && loading && <MatchCardSkeletonGrid count={6} />}
        {!error && !loading && predictions.length === 0 && (
          <EmptyState
            title="Sin señales con mercado válido para estos filtros"
            description="Prueba a quitar el filtro de liga/confianza, o ejecuta football-edge update-odds para traer cuotas nuevas."
          />
        )}
        {!error && !loading && predictions.length > 0 && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {predictions.map((p) => (
              <MatchCard key={p.id} prediction={p} />
            ))}
          </div>
        )}
      </section>

      <StatsFooter summary={summary} />
    </div>
  );
}
