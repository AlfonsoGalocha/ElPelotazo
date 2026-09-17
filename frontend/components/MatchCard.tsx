import Link from "next/link";
import type { Prediction } from "@/types";
import PredictionRow from "./PredictionRow";

export default function MatchCard({ predictions }: { predictions: Prediction[] }) {
  if (predictions.length === 0) return null;
  const match = predictions[0].match;
  const kickoff = new Date(match.kickoff_utc);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-3 flex items-center justify-between">
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
      <div>
        {predictions.map((p) => (
          <PredictionRow key={p.id} prediction={p} />
        ))}
      </div>
    </div>
  );
}
