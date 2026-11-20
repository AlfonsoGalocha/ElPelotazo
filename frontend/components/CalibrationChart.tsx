import type { PerformanceBucket } from "@/types";

function percent(value: number) {
  return `${(value * 100).toFixed(0)} %`;
}

export default function CalibrationChart({ buckets }: { buckets: PerformanceBucket[] }) {
  const rows = buckets.filter(row => row.n > 0 && row.predicted_probability_mean != null && row.empirical_frequency != null);
  if (!rows.length) return null;
  return (
    <div className="my-3 overflow-x-auto">
      <table className="w-full min-w-[300px] text-left text-xs">
        <thead className="text-slate-400"><tr>
          <th className="py-2">Probabilidad</th><th>Predijo</th><th>Ocurrió</th><th>Casos</th>
        </tr></thead>
        <tbody className="text-slate-200">
          {rows.map((row, index) => (
            <tr key={`${row.range}-${index}`} className="border-t border-surface-border">
              <td className="py-2">{row.range}</td>
              <td>{percent(row.predicted_probability_mean!)}</td>
              <td>{percent(row.empirical_frequency!)}</td>
              <td>{row.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
