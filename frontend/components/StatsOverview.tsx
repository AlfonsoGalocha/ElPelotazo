"use client";

import { useMemo } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { TrendingUp, Target, ListChecks, Coins } from "lucide-react";
import type { PerformanceSegment } from "@/types";
import { MARKET_DISPLAY_NAMES } from "@/types";

// Pedido explicito del rediseño: "pagina de Estadisticas visualmente
// potente" con ROI, yield, win rate, numero de predicciones, beneficio
// acumulado, rendimiento por mercado y por liga. TODO se deriva de
// `segments` (ya traido por /model/page.tsx, GET /models/performance) --
// nunca una metrica de ejemplo: si no hay suficientes apuestas con cuota
// real para un agregado concreto, ese agregado se omite, nunca se rellena
// con un 0 o un valor inventado.
export default function StatsOverview({ segments }: { segments: PerformanceSegment[] }) {
  const totals = useMemo(() => {
    let nSettled = 0;
    let nBets = 0;
    let totalStaked = 0;
    let totalPnl = 0;
    let hitWeightedSum = 0;
    let hitWeightCount = 0;

    for (const segment of segments) {
      nSettled += segment.n_settled;
      const strategy = segment.market_strategy;
      if (strategy?.n_bets) {
        nBets += strategy.n_bets;
        totalStaked += strategy.total_staked ?? 0;
        totalPnl += strategy.total_pnl ?? 0;
      }
      for (const bucket of segment.performance_by_edge_bucket) {
        if (bucket.hit_rate !== undefined && bucket.n > 0) {
          hitWeightedSum += bucket.hit_rate * bucket.n;
          hitWeightCount += bucket.n;
        }
      }
    }

    return {
      nSettled,
      nBets,
      totalPnl,
      roi: totalStaked > 0 ? totalPnl / totalStaked : null,
      winRate: hitWeightCount > 0 ? hitWeightedSum / hitWeightCount : null,
    };
  }, [segments]);

  // Bloque 5 de la mision (claridad): un ROI agregado sin su `n` al lado
  // es exactamente el mismo problema que una tasa de acierto sin
  // intervalo de confianza -- una liga con 2 apuestas liquidadas no puede
  // verse con el mismo peso visual que una con 200. `label` lleva el n
  // directamente en la etiqueta del eje (siempre visible, no solo al
  // pasar el raton) y el tooltip lo repite junto al ROI exacto.
  const byLeague = useMemo(() => {
    const grouped = new Map<string, { staked: number; pnl: number; nBets: number }>();
    for (const s of segments) {
      const strategy = s.market_strategy;
      if (!strategy?.n_bets) continue;
      const entry = grouped.get(s.competition_code) ?? { staked: 0, pnl: 0, nBets: 0 };
      entry.staked += strategy.total_staked ?? 0;
      entry.pnl += strategy.total_pnl ?? 0;
      entry.nBets += strategy.n_bets;
      grouped.set(s.competition_code, entry);
    }
    return [...grouped.entries()]
      .map(([code, { staked, pnl, nBets }]) => ({
        code,
        label: `${code} (n=${nBets})`,
        nBets,
        roi: staked > 0 ? (pnl / staked) * 100 : 0,
      }))
      .sort((a, b) => b.roi - a.roi);
  }, [segments]);

  const byMarket = useMemo(() => {
    const grouped = new Map<string, { staked: number; pnl: number; nBets: number }>();
    for (const s of segments) {
      const strategy = s.market_strategy;
      if (!strategy?.n_bets) continue;
      const marketLabel = MARKET_DISPLAY_NAMES[s.market] ?? s.market;
      const entry = grouped.get(marketLabel) ?? { staked: 0, pnl: 0, nBets: 0 };
      entry.staked += strategy.total_staked ?? 0;
      entry.pnl += strategy.total_pnl ?? 0;
      entry.nBets += strategy.n_bets;
      grouped.set(marketLabel, entry);
    }
    return [...grouped.entries()]
      .map(([market, { staked, pnl, nBets }]) => ({
        market,
        label: `${market} (n=${nBets})`,
        nBets,
        roi: staked > 0 ? (pnl / staked) * 100 : 0,
      }))
      .sort((a, b) => b.roi - a.roi);
  }, [segments]);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatTile
          icon={TrendingUp}
          label="ROI global (hipotético)"
          value={totals.roi !== null ? `${totals.roi >= 0 ? "+" : ""}${(totals.roi * 100).toFixed(1)}%` : "—"}
          positive={totals.roi !== null && totals.roi >= 0}
        />
        <StatTile
          icon={Target}
          label="Win rate (ponderado)"
          value={totals.winRate !== null ? `${(totals.winRate * 100).toFixed(1)}%` : "—"}
        />
        <StatTile icon={ListChecks} label="Predicciones liquidadas" value={String(totals.nSettled)} />
        <StatTile
          icon={Coins}
          label="Beneficio acumulado (stake=1)"
          value={totals.nBets > 0 ? `${totals.totalPnl >= 0 ? "+" : ""}${totals.totalPnl.toFixed(1)}u` : "—"}
          positive={totals.totalPnl >= 0}
        />
      </div>

      {(byLeague.length > 0 || byMarket.length > 0) && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {byLeague.length > 0 && (
            <div className="rounded-xl border border-surface-border bg-card p-4">
              <div className="mb-3 text-xs uppercase tracking-wide text-slate-500">ROI por liga</div>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={byLeague} layout="vertical" margin={{ left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2a44" horizontal={false} />
                  <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} unit="%" />
                  <YAxis type="category" dataKey="label" width={120} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: "#101729", border: "1px solid #1f2a44", borderRadius: 8 }}
                    formatter={(value: number, _name, item) => [
                      `${value.toFixed(1)}% (n=${item.payload.nBets} apuestas)`,
                      "ROI",
                    ]}
                  />
                  <Bar dataKey="roi" radius={[0, 4, 4, 0]}>
                    {byLeague.map((entry, i) => (
                      <Cell key={i} fill={entry.roi >= 0 ? "#20D995" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {byMarket.length > 0 && (
            <div className="rounded-xl border border-surface-border bg-card p-4">
              <div className="mb-3 text-xs uppercase tracking-wide text-slate-500">ROI por mercado</div>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={byMarket} layout="vertical" margin={{ left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2a44" horizontal={false} />
                  <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} unit="%" />
                  <YAxis type="category" dataKey="label" width={150} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: "#101729", border: "1px solid #1f2a44", borderRadius: 8 }}
                    formatter={(value: number, _name, item) => [
                      `${value.toFixed(1)}% (n=${item.payload.nBets} apuestas)`,
                      "ROI",
                    ]}
                  />
                  <Bar dataKey="roi" radius={[0, 4, 4, 0]}>
                    {byMarket.map((entry, i) => (
                      <Cell key={i} fill={entry.roi >= 0 ? "#20D995" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  positive,
}: {
  icon: typeof TrendingUp;
  label: string;
  value: string;
  positive?: boolean;
}) {
  return (
    <div className="rounded-xl border border-surface-border bg-card p-4">
      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div
        className={
          "mt-2 text-2xl font-bold " +
          (positive === undefined ? "text-slate-100" : positive ? "text-edge-positive" : "text-red-400")
        }
      >
        {value}
      </div>
    </div>
  );
}
