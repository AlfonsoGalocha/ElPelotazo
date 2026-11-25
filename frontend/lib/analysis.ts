import { apiFetch } from "@/lib/api";

// Home/away identity colours, validated for the dark card surface (#101729).
export const HOME_COLOR = "#0b93d1";
export const AWAY_COLOR = "#d97706";

export type Describe = { n: number; mean: number | null; median: number | null; std: number | null;
  min: number | null; max: number | null; distribution: Record<string, number> };
export type Rate = { count: number; n: number; rate: number | null };
export type LineFreq = Record<string, { over: number; n: number; rate: number | null }>;

export type TeamRow = {
  match_id: number; kickoff: string; competition: string; competition_name: string; venue: "home" | "away";
  opponent_id: number; opponent: string; opponent_logo: string | null; result: "W" | "D" | "L" | null;
  goals_for: number | null; goals_against: number | null; goals_ht_for: number | null; goals_ht_against: number | null;
  corners_for: number | null; corners_against: number | null; shots_for: number | null; shots_against: number | null;
  shots_on_target_for: number | null; shots_on_target_against: number | null;
  possession_for: number | null; possession_against: number | null; age_days?: number;
};

export type Profile = {
  matches: number; record: { W: number; D: number; L: number };
  clean_sheets: Rate; scored: Rate; conceded: Rate; btts: Rate;
  goals_for: Describe; goals_against: Describe; goals_total: Describe; goals_ht_for: Describe;
  goals_ht_total: Describe; goals_2h_for: Describe; goals_lines: LineFreq; team_goals_lines: LineFreq;
  corners_for: Describe; corners_against: Describe; corners_total: Describe; corners_lines: LineFreq;
  team_corners_lines: LineFreq; shots_for: Describe; shots_against: Describe; shots_total: Describe;
  shots_lines: LineFreq; shots_on_target_for: Describe; shots_on_target_against: Describe;
  shots_on_target_total: Describe; shots_on_target_lines: LineFreq; shots_off_target_for: Describe;
  possession: Describe;
  conversion: { goals_per_shot: number | null; goals_per_shot_on_target: number | null; n: number };
};

export type TeamAnalysis = { filters: Record<string, unknown>; requested: number; profile: Profile; matches: TeamRow[] };
export type AnalysisTeam = { id: number; name: string; logo_url: string | null };
export type Standing = { position: number; points: number; played: number; won: number; drawn: number; lost: number;
  goals_for: number; goals_against: number; as_of: string } | null;

export type ModelProbability = { original: number | null; calibrated: number | null; uncertainty: (number | null)[] | null;
  model_version_id: number | null; model_name: string | null; created_at: string; source: string };

export type MatchAnalysis = {
  match: { id: number; kickoff: string; status: string; competition: { code: string; name: string; country: string };
    matchday: number | null; round_name: string | null; home_goals: number | null; away_goals: number | null };
  filters: { window: number; venue: "split" | "all"; scope: "all" | "competition"; season: "all" | "current" };
  temporal_note: string;
  home: { team: AnalysisTeam; standing: Standing; form: (string | null)[]; analysis: TeamAnalysis;
    coverage: Record<string, { n: number; of: number }> };
  away: MatchAnalysis["home"];
  h2h: { matches: TeamRow[]; profile: Profile };
  markets: Record<string, { model: ModelProbability | null; market: { price: number; implied: number; bookmakers: number; best: number } | null;
    evidence: { source_level: string; n_observations: number; value: number } | null }>;
  final: Record<string, number | null> | null;
};

export type AnalysisFilters = { window: number; venue: "split" | "all"; scope: "all" | "competition"; season: "all" | "current" };

export function getMatchAnalysis(matchId: number, f: AnalysisFilters) {
  return apiFetch<MatchAnalysis>(`/analysis/matches/${matchId}?window=${f.window}&venue=${f.venue}&scope=${f.scope}&season=${f.season}`);
}

export type OddsRow = {
  market: string; line: number | null; selection: string; model_market: string | null;
  consensus: number | null; implied: number | null; no_vig: number | null; model_probability: number | null;
  model_gap: number | null;
  first: { price: number; bookmaker: string; recorded_at: string; origin_at: string | null } | null;
  bookmakers: { bookmaker: string; price: number; origin_at: string | null; fetched_at: string | null; freshness: string }[];
  series: { at: string; bookmaker: string; price: number }[];
};
export type MatchOddsCentre = { match_id: number; kickoff: string; status: string; note: string; rows: OddsRow[] };

export function getMatchOddsCentre(matchId: number) {
  return apiFetch<MatchOddsCentre>(`/analysis/matches/${matchId}/odds`);
}

export type TeamProfile = {
  team: AnalysisTeam; competitions: { code: string; name: string }[];
  standings: { competition: string; name: string; position: number; points: number; played: number; as_of: string }[];
  filters: { window: number; competition: string | null };
  overall: TeamAnalysis; home: TeamAnalysis; away: TeamAnalysis; recent: TeamRow[];
  upcoming: { id: number; kickoff: string; competition: string; home: AnalysisTeam; away: AnalysisTeam }[];
};

export function getTeamProfile(teamId: number, window = 10, competition?: string) {
  return apiFetch<TeamProfile>(`/analysis/teams/${teamId}?window=${window}${competition ? `&competition=${competition}` : ""}`);
}

export type TeamDirectoryEntry = AnalysisTeam & { competitions: { code: string; name: string }[] };
export function getTeamDirectory() {
  return apiFetch<TeamDirectoryEntry[]>("/analysis/teams");
}

export type ExplorerRow = {
  match_id: number; kickoff: string; competition: { code: string; name: string };
  home: AnalysisTeam; away: AnalysisTeam; value: number; home_value: number | null; away_value: number | null;
  sample: { home: number; away: number; requested: number }; counts: { home: number; away: number } | null;
};
export type ExplorerResult = { rows: ExplorerRow[]; kind: "estadístico" | "predictivo"; metric: string; label: string;
  family: string | null; line: number | null; window: number; venue: string; min_sample: number; order: string;
  date_from: string; date_to: string };

export function getExplorer(params: Record<string, string | number | undefined>) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== "") qs.set(key, String(value)); });
  return apiFetch<ExplorerResult>(`/analysis/explorer?${qs}`);
}

export type CoverageRow = { competition: string; name: string; season: string; finished: number; halftime: number;
  corners: number; shots: number; shots_on_target: number; possession: number; shots_partial: number;
  shots_on_target_partial: number };
export function getCoverage() {
  return apiFetch<CoverageRow[]>("/analysis/coverage");
}

export const fmt = (value: number | null | undefined, digits = 2) =>
  value == null ? "—" : value.toLocaleString("es-ES", { maximumFractionDigits: digits });
export const pct = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toLocaleString("es-ES", { maximumFractionDigits: 0 })} %`;
export const lineLabel = (line: string | number) => String(line).replace(".", ",");

export type CompetitionFixture = { id: number; kickoff: string; home: AnalysisTeam; away: AnalysisTeam;
  home_goals: number | null; away_goals: number | null; round: string | number | null };
export type CompetitionOverview = {
  competition: { code: string; name: string; country: string };
  standings: { team: AnalysisTeam; position: number; played: number; won: number; drawn: number; lost: number;
    goals_for: number; goals_against: number; goal_difference: number; points: number; as_of: string }[];
  upcoming: CompetitionFixture[]; recent: CompetitionFixture[]; coverage: CoverageRow[];
};
export function getCompetitionOverview(code: string) {
  return apiFetch<CompetitionOverview>(`/analysis/competitions/${code}`);
}

export type StrategyPick = {
  market: string; probability: number; original_probability: number | null; odds: number | null; bookmaker: string | null;
  ev: number | null; score: number | null; eligible: boolean; reasons: string[]; confidence_reasons: string[];
  is_correct: boolean | null;
};
export type StrategyPicks = { source: "en_vivo" | "archivo_prepartido" | "sin_datos"; evaluated_at: string | null;
  rule: string; eligible_count: number; picks: StrategyPick[] };
export function getStrategyPicks(matchId: number) {
  return apiFetch<StrategyPicks>(`/analysis/matches/${matchId}/strategy-picks`);
}
