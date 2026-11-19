"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PublicationSelection } from "@/lib/api";

export default function OpportunityEquity({ rows }: { rows: PublicationSelection[] }) {
  const data = [...rows].reverse().filter(row => row.cumulative_units !== undefined)
    .map(row => ({ date: row.kickoff.slice(0, 10), units: row.cumulative_units }));
  if (!data.length) return null;
  return (
    <section className="rounded-xl border border-surface-border p-4">
      <h2 className="mb-2 font-semibold">Beneficio teórico acumulado · n={data.length}</h2>
      <div className="h-60">
        <ResponsiveContainer>
          <LineChart data={data}>
            <CartesianGrid vertical={false} stroke="#334155" strokeDasharray="3 3" />
            <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <YAxis unit=" u" tick={{ fill: "#94a3b8", fontSize: 12 }} />
            <Tooltip formatter={(value: number) => [`${value.toFixed(2)} u`, "Beneficio teórico"]} />
            <Line type="linear" dataKey="units" stroke="#60a5fa" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
