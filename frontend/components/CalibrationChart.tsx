"use client";

import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PerformanceBucket } from "@/types";

// Fase 6 (seccion 10/11 del brief): diagrama de fiabilidad (reliability
// diagram) -- "Predicho" es la propia probabilidad media del bucket (la
// diagonal de referencia: un modelo perfectamente calibrado tendria
// "Real" exactamente encima), "Real" es la frecuencia empirica realmente
// observada en predicciones YA LIQUIDADAS (nunca un backtest sintetico,
// ver evaluation_service.py::real_performance_report). Reemplaza las
// barras de progreso + tabla de ModelPerformanceBoard por un grafico
// interactivo real (tooltip con los numeros exactos al pasar el raton).
export default function CalibrationChart({ buckets }: { buckets: PerformanceBucket[] }) {
  const data = buckets.map((b) => ({
    range: b.range ?? "—",
    n: b.n,
    predicho: Math.round((b.predicted_probability_mean ?? 0) * 1000) / 10,
    real: Math.round((b.empirical_frequency ?? 0) * 1000) / 10,
  }));

  return (
    <div style={{ width: "100%", height: 220 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2530" vertical={false} />
          <XAxis dataKey="range" tick={{ fill: "#94a3b8", fontSize: 11 }} tickLine={false} axisLine={{ stroke: "#1f2530" }} />
          <YAxis
            domain={[0, 100]}
            unit="%"
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          <Tooltip
            contentStyle={{ background: "#11151d", border: "1px solid #1f2530", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value: number, name: string) => [`${value.toFixed(1)}%`, name]}
            labelFormatter={(range: string, payload) => {
              const n = payload?.[0]?.payload?.n;
              return n != null ? `${range} (n=${n})` : range;
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "#94a3b8" }} />
          <Line
            type="monotone"
            dataKey="predicho"
            name="Predicho (referencia)"
            stroke="#60a5fa"
            strokeDasharray="4 3"
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="real"
            name="Real (liquidadas)"
            stroke="#34d399"
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
