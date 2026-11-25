"use client";

import { useState } from "react";
import type { MatchHistory, PerformanceSegment } from "@/types";
import HistoryBoard from "./HistoryBoard";
import ModelPerformanceBoard from "./ModelPerformanceBoard";
import ModelVersionsBoard from "./ModelVersionsBoard";
import SelectorReliabilityBoard from "./SelectorReliabilityBoard";
import StatsOverview from "./StatsOverview";
import { EmptyState } from "./EmptyState";

export type Tab = "resumen" | "jornadas" | "competiciones" | "versiones" | "fiabilidad-selector";

const TABS: { key: Tab; label: string }[] = [
  { key: "resumen", label: "Resumen global" },
  { key: "jornadas", label: "Jornadas" },
  { key: "competiciones", label: "Competiciones y mercados" },
  { key: "versiones", label: "Versiones del modelo" },
  { key: "fiabilidad-selector", label: "Fiabilidad del selector" },
];

// Las 4 subsecciones pedidas por el usuario para "Rendimiento del
// modelo". Estado local (sin sincronizar con la URL a proposito): son
// pestañas de UNA misma pantalla, no filtros que alguien vaya a
// compartir por enlace -- cambiar de pestaña nunca vuelve a pedir nada
// al backend que ya se trajo (segments/historyRows vienen del Server
// Component padre), excepto "Versiones del modelo", que tiene su propia
// fuente (/models) y se trae sola al activarse esa pestaña.
export default function ModelPerformanceTabs({
  segments,
  historyRows,
  initialTab = "resumen",
}: {
  segments: PerformanceSegment[];
  historyRows: MatchHistory[];
  initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap gap-1 border-b border-surface-border">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={
              "px-3 py-2 text-sm font-medium transition-colors " +
              (tab === t.key
                ? "border-b-2 border-accent text-slate-100"
                : "text-slate-500 hover:text-slate-300")
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "resumen" &&
        (segments.length === 0 ? (
          <EmptyState
            title="Todavía no hay predicciones liquidadas"
            description="Ejecuta football-edge refresh tras jugarse partidos reales."
          />
        ) : (
          <div className="flex flex-col gap-6">
            <StatsOverview segments={segments} />
          </div>
        ))}

      {tab === "jornadas" &&
        (historyRows.length === 0 ? (
          <EmptyState
            title="Sin partidos finalizados todavía"
            description="Ejecuta football-edge refresh cuando haya resultados reales nuevos."
          />
        ) : (
          <HistoryBoard rows={historyRows} />
        ))}

      {tab === "competiciones" &&
        (segments.length === 0 ? (
          <EmptyState
            title="Todavía no hay predicciones liquidadas"
            description="Ejecuta football-edge refresh tras jugarse partidos reales."
          />
        ) : (
          <ModelPerformanceBoard segments={segments} />
        ))}

      {tab === "versiones" && <ModelVersionsBoard />}

      {tab === "fiabilidad-selector" && <SelectorReliabilityBoard />}
    </div>
  );
}
