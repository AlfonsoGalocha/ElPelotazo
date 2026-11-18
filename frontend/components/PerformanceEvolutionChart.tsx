"use client";

import { useEffect, useState } from "react";
import { Line, LineChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getHistory } from "@/lib/api";
import { formatMatchDateOnly } from "@/lib/dates";
import type { HistoryPrediction, MatchHistory } from "@/types";

// Subseccion "Resumen global" de "Rendimiento del modelo" (pedido real
// del usuario: "Gráficos de evolución"). Beneficio TEORICO acumulado
// (stake=1 por partido, solo sobre la MEJOR predicción de cada uno, con
// cuota de mercado real) ordenado por fecha de kickoff -- nunca se
// inventa una curva: las predicciones "void" (partido cancelado) y las
// sin cuota real simplemente no aportan nada a la curva, ni ganancia ni
// perdida, en vez de contarse como 0 arbitrario.
function bestSettledPrediction(row: MatchHistory): HistoryPrediction | undefined {
  return row.predictions.find((p) => p.id === row.best_prediction_id);
}

function pnlFor(prediction: HistoryPrediction | undefined): number | null {
  if (!prediction || prediction.market_odds == null) return null;
  if (prediction.settlement_status === "void") return null; // stake devuelto: no suma ni resta
  if (prediction.is_correct === null) return null; // pendiente
  return prediction.is_correct ? prediction.market_odds - 1 : -1;
}

export default function PerformanceEvolutionChart() {
  const [history, setHistory] = useState<MatchHistory[] | null>(null);

  useEffect(() => {
    getHistory(undefined, 300)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, []);

  if (history === null) {
    return <div className="h-56 animate-pulse rounded-xl border border-surface-border bg-card" />;
  }

  const sorted = [...history].sort(
    (a, b) => new Date(a.match.kickoff_utc).getTime() - new Date(b.match.kickoff_utc).getTime()
  );

  let cumulative = 0;
  let nBets = 0;
  const data: { date: string; label: string; pnl: number }[] = [];
  for (const row of sorted) {
    const pnl = pnlFor(bestSettledPrediction(row));
    if (pnl === null) continue;
    cumulative += pnl;
    nBets += 1;
    data.push({
      date: row.match.kickoff_utc,
      label: formatMatchDateOnly(row.match.kickoff_utc, { day: "2-digit", month: "short" }),
      pnl: Math.round(cumulative * 100) / 100,
    });
  }

  if (data.length === 0) {
    return (
      <div className="rounded-xl border border-surface-border bg-card p-6 text-sm text-slate-500">
        Todavía no hay suficientes predicciones liquidadas con cuota real para dibujar la evolución.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-surface-border bg-card p-4">
      <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
        Beneficio teórico acumulado (stake=1, mejor predicción de cada partido)
      </div>
      <div className="mb-3 text-[11px] text-slate-500">
        {nBets} predicciones con cuota real liquidadas · últimos {sorted.length} partidos finalizados
      </div>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2530" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "#1f2530" }}
              minTickGap={40}
            />
            <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} width={40} unit="u" />
            <Tooltip
              contentStyle={{ background: "#11151d", border: "1px solid #1f2530", borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: "#e2e8f0" }}
              formatter={(value: number) => [`${value >= 0 ? "+" : ""}${value.toFixed(2)}u`, "Beneficio acum."]}
            />
            <ReferenceLine y={0} stroke="#64748b" />
            <Line
              type="monotone"
              dataKey="pnl"
              stroke={cumulative >= 0 ? "#20D995" : "#ef4444"}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
