export interface Team {
  id: number;
  canonical_name: string;
}

export interface Competition {
  id: number;
  code: string;
  name: string;
  country: string;
}

export interface Match {
  id: number;
  competition: Competition;
  home_team: Team;
  away_team: Team;
  kickoff_utc: string;
  status: "scheduled" | "finished";
  home_goals: number | null;
  away_goals: number | null;
}

export interface PredictionFactor {
  feature: string;
  display_name: string;
  contribution: number | null;
  direction: "increases_probability" | "decreases_probability";
  raw_value: number | null;
}

export type SignalTier = "HIGH_DATA_SUPPORT" | "MEDIUM_DATA_SUPPORT" | "LOW_DATA_SUPPORT";

export interface Prediction {
  id: number;
  match: Match;
  market: string;
  line: number | null;
  selection: string;
  model_probability: number;
  market_probability: number | null;
  market_odds: number | null;
  fair_odds: number;
  edge: number | null;
  expected_value: number | null;
  confidence: number;
  data_quality: number;
  signal_tier: SignalTier;
  explanation: PredictionFactor[];
  model_version_id: number;
  created_at: string;
}

export const MARKET_DISPLAY_NAMES: Record<string, string> = {
  over_1_5: "Over 1.5 Goals",
  over_2_5: "Over 2.5 Goals",
  under_2_5: "Under 2.5 Goals",
  over_3_5: "Over 3.5 Goals",
  btts: "Both Teams To Score",
  cards_over_3_5: "Over 3.5 Cards",
  cards_over_4_5: "Over 4.5 Cards",
  cards_over_5_5: "Over 5.5 Cards",
  cards_under_4_5: "Under 4.5 Cards",
  corners_over_8_5: "Over 8.5 Corners",
  corners_over_9_5: "Over 9.5 Corners",
  corners_over_10_5: "Over 10.5 Corners",
  corners_under_9_5: "Under 9.5 Corners",
};

export const GOALS_MARKETS = ["over_1_5", "over_2_5", "under_2_5", "over_3_5", "btts"];
export const CARDS_MARKETS = ["cards_over_3_5", "cards_over_4_5", "cards_over_5_5", "cards_under_4_5"];
export const CORNERS_MARKETS = [
  "corners_over_8_5",
  "corners_over_9_5",
  "corners_over_10_5",
  "corners_under_9_5",
];

export function marketFamily(market: string): "goals" | "cards" | "corners" {
  if (market.startsWith("cards_")) return "cards";
  if (market.startsWith("corners_")) return "corners";
  return "goals";
}
