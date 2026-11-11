"use client";

import { useState } from "react";
import Link from "next/link";
import type { Prediction } from "@/types";
import { CARDS_MARKETS, CORNERS_MARKETS, GOALS_MARKETS, MATCH_RESULT_MARKETS } from "@/types";
import MatchSummary from "./MatchSummary";
import PredictionRow from "./PredictionRow";

const TABS: { key: "result" | "goals" | "cards" | "corners"; label: string; markets: string[] }[] = [
  { key: "result", label: "1X2", markets: MATCH_RESULT_MARKETS },
  { key: "goals", label: "Goles", markets: GOALS_MARKETS },
  { key: "cards", label: "Tarjetas", markets: CARDS_MARKETS },
  { key: "corners", label: "Córners", markets: CORNERS_MARKETS },
];

export default function MatchCard({ predictions }: { predictions: Prediction[] }) {
  const [tab, setTab] = useState<"result" | "goals" | "cards" | "corners">("result");
  // Vista simple por defecto (feedback directo: "demasiados numeros/
  // terminos tecnicos por partido") -- todos los mercados siguen aqui,
  // solo un clic detras, nunca se borra nada.
  const [showAllMarkets, setShowAllMarkets] = useState(false);
  if (predictions.length === 0) return null;
  const match = predictions[0].match;
  const kickoff = new Date(match.kickoff_utc);

  const activeMarkets = TABS.find((t) => t.key === tab)!.markets;
  const visible = predictions.filter((p) => activeMarkets.includes(p.market));

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Link href={`/matches/${match.id}`} className="hover:underline">
          <div className="text-base font-semibold text-slate-100">
            {match.home_team.canonical_name} <span className="text-slate-500">vs</span>{" "}
            {match.away_team.canonical_name}
          </div>
        </Link>
        <div className="text-xs text-slate-500">
          {match.competition.name} ·{" "}
          {kickoff.toLocaleString("es-ES", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
        </div>
      </div>

      <MatchSummary predictions={predictions} />

      <button
        onClick={() => setShowAllMarkets((v) => !v)}
        className="mt-3 text-xs text-slate-500 underline hover:text-slate-300"
      >
        {showAllMarkets ? "Ocultar todos los mercados" : "Ver todos los mercados"}
      </button>

      {showAllMarkets && (
        <div className="mt-3 border-t border-surface-border pt-3">
          <div className="mb-2 flex gap-1 border-b border-surface-border">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-3 py-1.5 text-xs font-medium uppercase tracking-wide ${
                  tab === t.key
                    ? "border-b-2 border-slate-200 text-slate-100"
                    : "text-slate-500 hover:text-slate-300"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div>
            {visible.map((p) => (
              <PredictionRow key={p.id} prediction={p} />
            ))}
            {visible.length === 0 && (
              <div className="py-3 text-xs text-slate-500">Sin predicciones para esta categoría.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
