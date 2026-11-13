"use client";

import { Bar, BarChart, Cell, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PerformanceBucket } from "@/types";

// Fase 6 (seccion 10/11 del brief): ROI REAL por rango de edge, solo sobre
// predicciones liquidadas con cuota de mercado disponible (ver
// evaluation_service.py::real_performance_report -> performance_by_edge_bucket).
// Deliberadamente NO se asume "a mas edge, mejor resultado" (ver comentario
// ya existente en ModelPerformanceBoard) -- el grafico se limita a mostrar
// lo que de verdad paso, bucket por bucket, sin ninguna tendencia dibujada
// a mano. La linea en 0% marca el punto de equilibrio: una barra por debajo
// es una perdida real en esa muestra.
export default function EdgeRoiChart({ buckets }: { buckets: PerformanceBucket[] }) {
  const data = buckets.map((b) => ({
    range: b.edge_range_pp ?? "—",
    n: b.n,
    hitRate: Math.round((b.hit_rate ?? 0) * 1000) / 10,
    brier: b.brier_score ?? 0,
    roi: Math.round((b.roi ?? 0) * 1000) / 10,
  }));

  return (
    <div style={{ width: "100%", height: 220 }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2530" vertical={false} />
          <XAxis
            dataKey="range"
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "#1f2530" }}
            label={{ value: "edge (pp)", position: "insideBottom", offset: -2, fill: "#64748b", fontSize: 11 }}
          />
          <YAxis unit="%" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={false} width={40} />
          <Tooltip
            contentStyle={{ background: "#11151d", border: "1px solid #1f2530", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value: number, name: string) => {
              if (name === "roi") return [`${value.toFixed(1)}%`, "ROI"];
              return [value, name];
            }}
            labelFormatter={(range: string, payload) => {
              const p = payload?.[0]?.payload;
              return p ? `Edge ${range} · n=${p.n} · acierto ${p.hitRate.toFixed(1)}% · Brier ${p.brier.toFixed(3)}` : range;
            }}
          />
          <ReferenceLine y={0} stroke="#64748b" />
          <Bar dataKey="roi" name="roi" radius={3} isAnimationActive={false}>
            {data.map((row, i) => (
              <Cell key={i} fill={row.roi >= 0 ? "#22c55e" : "#ef4444"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
