"use client";

import { useCachedData } from "@/lib/clientCache";
import { getMatchAnalysis, type MatchAnalysis } from "@/lib/analysis";
import { FormString } from "./StatsKit";

const DEFAULT = { window: 10, venue: "split", scope: "all", season: "all" } as const;

/** Standing and recent form under each team in the match header (same request as the default analysis tab). */
export default function MatchHeaderContext({ matchId, side }: { matchId: number; side: "home" | "away" }) {
  const key = `analysis:${matchId}:${DEFAULT.window}:${DEFAULT.venue}:${DEFAULT.scope}:${DEFAULT.season}`;
  const { data } = useCachedData<MatchAnalysis>(key, () => getMatchAnalysis(matchId, DEFAULT));
  const team = data?.[side];
  if (!team) return <span className="block h-5" aria-hidden />;
  return (
    <span className={`flex items-center gap-2 text-xs text-slate-400 ${side === "away" ? "flex-row-reverse" : ""}`}>
      {team.standing && <span title={`${team.standing.points} puntos tras ${team.standing.played} partidos`}>{team.standing.position}.º</span>}
      <FormString results={team.form} />
    </span>
  );
}
