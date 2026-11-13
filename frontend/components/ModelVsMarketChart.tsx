"use client";

import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, XAxis, YAxis } from "recharts";
import type { Prediction } from "@/types";

interface Row {
  label: string;
  value: number;
  color: string;
}

// Comparativa visual modelo-vs-mercado pedida en el rediseño (seccion
// "detalle de partido"): un UNICO eje (probabilidad 0-100%, nunca dos
// escalas distintas), una barra por cada probabilidad REALMENTE
// disponible -- "Calibrado" solo aparece si `calibrated_probability` no
// es null (nunca se inventa una calibracion que no existe), "Mercado"
// solo si hay cuota real. El color es decorativo aqui (cada fila ya
// lleva su propia etiqueta de texto): nunca la unica forma de
// distinguirlas.
export default function ModelVsMarketChart({ prediction }: { prediction: Prediction }) {
  const rows: Row[] = [{ label: "Modelo", value: prediction.model_probability * 100, color: "#60a5fa" }];
  if (prediction.calibrated_probability !== null) {
    rows.push({ label: "Calibrado", value: prediction.calibrated_probability * 100, color: "#34d399" });
  }
  if (prediction.market_probability !== null) {
    rows.push({ label: "Mercado", value: prediction.market_probability * 100, color: "#94a3b8" });
  }

  return (
    <div className="flex flex-col gap-2">
      <div style={{ width: "100%", height: rows.length * 36 + 16 }}>
        <ResponsiveContainer>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 32, bottom: 4, left: 0 }}>
            <XAxis type="number" domain={[0, 100]} hide />
            <YAxis
              type="category"
              dataKey="label"
              width={72}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "#94a3b8", fontSize: 12 }}
            />
            <Bar dataKey="value" radius={4} barSize={18} isAnimationActive={false}>
              {rows.map((row) => (
                <Cell key={row.label} fill={row.color} />
              ))}
              <LabelList
                dataKey="value"
                position="right"
                formatter={(v: number) => `${v.toFixed(1)}%`}
                fill="#e2e8f0"
                fontSize={12}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      {prediction.edge !== null && (
        <div className="text-xs text-slate-500">
          Edge (modelo − mercado) ={" "}
          <span className={prediction.edge >= 0 ? "text-edge-positive" : "text-edge-negative"}>
            {prediction.edge >= 0 ? "+" : ""}
            {(prediction.edge * 100).toFixed(1)}pp
          </span>
          {" · "}
          cuota justa {prediction.fair_odds.toFixed(2)}
          {prediction.market_odds !== null && <> · cuota de mercado {prediction.market_odds.toFixed(2)}</>}
        </div>
      )}
    </div>
  );
}
