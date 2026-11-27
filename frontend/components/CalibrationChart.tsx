"use client";

import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import type { PerformanceBucket } from "@/types";

// v2.8.3 (pedido real del usuario, "Rendimiento del modelo"): antes esto
// era solo una tabla (predijo/ocurrió/casos). Mismos datos, sin ningun
// numero nuevo ni inventado -- un punto por cada bucket real que ya
// calculaba el backend (ver evaluation_service.py), situado en
// (probabilidad predicha, frecuencia observada). La diagonal punteada es
// la calibracion perfecta (lo predicho = lo ocurrido): un punto por
// encima de la diagonal significa que el modelo predijo de menos para
// ese rango, por debajo que predijo de mas. El tamaño del punto es el
// tamaño de la muestra (`n`) -- nunca se oculta, ni en el grafico ni en
// la tabla de abajo.
export default function CalibrationChart({ buckets }: { buckets: PerformanceBucket[] }) {
  const rows = buckets.filter((row) => row.n > 0 && row.predicted_probability_mean != null && row.empirical_frequency != null);
  if (!rows.length) return null;

  const data = rows.map((row, index) => ({
    range: row.range ?? "",
    predicted: Math.round(row.predicted_probability_mean! * 1000) / 10,
    observed: Math.round(row.empirical_frequency! * 1000) / 10,
    n: row.n,
    key: `${row.range}-${index}`,
  }));
  const maxN = Math.max(...data.map((d) => d.n));

  return (
    <div>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 16, bottom: 20, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2a44" />
            <XAxis
              type="number"
              dataKey="predicted"
              domain={[0, 100]}
              unit="%"
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: "#1f2a44" }}
              label={{ value: "Probabilidad predicha", position: "insideBottom", offset: -8, fill: "#64748b", fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="observed"
              domain={[0, 100]}
              unit="%"
              tick={{ fill: "#94a3b8", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={46}
              label={{ value: "Frecuencia real", angle: -90, position: "insideLeft", fill: "#64748b", fontSize: 11 }}
            />
            <ZAxis dataKey="n" range={[40, 360]} domain={[0, maxN]} name="muestra" />
            <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="#64748b" strokeDasharray="4 4" ifOverflow="extendDomain" />
            <Tooltip
              cursor={{ stroke: "#713CFF", strokeOpacity: 0.3 }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as (typeof data)[number];
                return (
                  <div className="rounded-lg border border-surface-border bg-card px-3 py-2 text-xs">
                    <div className="font-medium text-slate-200">{p.range}</div>
                    <div className="text-slate-400">
                      Predijo {p.predicted.toFixed(1)}% · Ocurrió {p.observed.toFixed(1)}%
                    </div>
                    <div className="text-slate-500">n = {p.n}</div>
                  </div>
                );
              }}
            />
            <Scatter data={data} fill="#713CFF" fillOpacity={0.75} isAnimationActive={false} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[11px] text-slate-500">
        Línea punteada = calibración perfecta. Tamaño del punto = tamaño de la muestra.
      </p>
      <details className="mt-2 text-xs text-slate-400">
        <summary className="cursor-pointer text-slate-500 hover:text-slate-300">Ver valores exactos</summary>
        <table className="mt-2 w-full min-w-[300px] text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-1">Probabilidad</th>
              <th>Predijo</th>
              <th>Ocurrió</th>
              <th>Casos</th>
            </tr>
          </thead>
          <tbody className="text-slate-300">
            {rows.map((row, index) => (
              <tr key={`${row.range}-${index}`} className="border-t border-surface-border/60">
                <td className="py-1">{row.range}</td>
                <td>{(row.predicted_probability_mean! * 100).toFixed(0)} %</td>
                <td>{(row.empirical_frequency! * 100).toFixed(0)} %</td>
                <td>{row.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
