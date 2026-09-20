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
  matchday: number | null;
}

export interface RoundInfo {
  competition_code: string;
  round: number | null;
  round_start: string | null;
  round_end: string | null;
  next_round: number | null;
  is_fallback: boolean;
  match_ids: number[];
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
  has_market: boolean;
  market_probability_source: string | null;
  bookmakers_count: number | null;
  bookmakers_used: number | null;
  market_odds_min: number | null;
  market_odds_max: number | null;
  market_odds_median: number | null;
  market_odds_average: number | null;
  market_quality: "HIGH" | "MEDIUM" | "LOW" | null;
  odds_age_minutes: number | null;
  is_stale_odds: boolean;
  // Fase 3 (seccion 18): etiquetas de anomalia calculadas contra las demas
  // predicciones del MISMO partido (ver backend/app/prediction/anomaly.py).
  anomaly_flags: AnomalyFlag[];
  // Fase 3 (seccion 1): true cuando es la de mayor signal_score entre las
  // que pasan el filtro duro y no tienen CONTRADICCION, para su partido.
  is_best_prediction: boolean;
}

export type AnomalyFlag =
  | "CONTRADICCION"
  | "SENAL_BAJA_FIABILIDAD"
  | "OUTLIER"
  | "HIGH_PROBABILITY_LOW_VALUE";

// Fase 4: prediccion de un partido YA FINALIZADO, con el resultado real
// (liquidado por backend/app/services/evaluation_service.py, nunca
// inferido en el frontend).
export interface HistoryPrediction extends Prediction {
  actual_result: string | null;
  is_correct: boolean | null;
  settled_at: string | null;
}

export interface MatchHistory {
  match: Match;
  predictions: HistoryPrediction[];
  best_prediction_id: number | null;
}

// Fase 3 (seccion 16): respuesta de /predictions/best-of-day.
export interface BestOfDay {
  prediction: Prediction | null;
  explanation: string[];
}

// Fase 5 (seccion 10/11): un segmento de /models/performance.
export interface PerformanceBucket {
  range?: string;
  edge_range_pp?: string;
  n: number;
  predicted_probability_mean?: number;
  empirical_frequency?: number;
  average_edge_pp?: number;
  hit_rate?: number;
  brier_score?: number;
  roi?: number;
}

export interface PerformanceSegment {
  competition_code: string;
  market: string;
  model_version_id: number;
  n_settled: number;
  model_quality: {
    n_predictions: number;
    brier_score: number;
    log_loss: number;
    expected_calibration_error: number;
    reliability_curve: unknown;
    accuracy_secondary_only: number;
  };
  market_strategy: {
    n_opportunities: number;
    n_bets: number;
    total_staked?: number;
    total_pnl?: number;
    roi?: number;
    average_edge?: number;
    max_drawdown?: number;
  } | null;
  performance_by_probability_bucket: PerformanceBucket[];
  performance_by_edge_bucket: PerformanceBucket[];
}

export interface ModelPerformanceReport {
  segments: PerformanceSegment[];
}

export interface BookmakerOdds {
  bookmaker: string;
  price: number;
  snapshot_type: string;
  diff_from_consensus: number | null;
  is_outlier: boolean;
}

export interface SignalDetail extends Prediction {
  bookmaker_odds: BookmakerOdds[];
  explanation_summary: string[];
}

export const MARKET_DISPLAY_NAMES: Record<string, string> = {
  over_1_5: "Más de 1.5 goles",
  over_2_5: "Más de 2.5 goles",
  under_2_5: "Menos de 2.5 goles",
  over_3_5: "Más de 3.5 goles",
  btts: "Ambos equipos marcan",
  home_win: "Victoria local (1)",
  draw: "Empate (X)",
  away_win: "Victoria visitante (2)",
  double_chance_1x: "Doble oportunidad 1X",
  double_chance_x2: "Doble oportunidad X2",
  double_chance_12: "Doble oportunidad 12",
  home_team_over_0_5: "Local marca",
  home_team_over_1_5: "Local más de 1.5 goles",
  away_team_over_0_5: "Visitante marca",
  away_team_over_1_5: "Visitante más de 1.5 goles",
  cards_over_3_5: "Más de 3.5 tarjetas",
  cards_over_4_5: "Más de 4.5 tarjetas",
  cards_over_5_5: "Más de 5.5 tarjetas",
  cards_under_4_5: "Menos de 4.5 tarjetas",
  corners_over_8_5: "Más de 8.5 córners",
  corners_over_9_5: "Más de 9.5 córners",
  corners_over_10_5: "Más de 10.5 córners",
  corners_under_9_5: "Menos de 9.5 córners",
};

export const GOALS_MARKETS = [
  "over_1_5",
  "over_2_5",
  "under_2_5",
  "over_3_5",
  "btts",
  "home_team_over_0_5",
  "home_team_over_1_5",
  "away_team_over_0_5",
  "away_team_over_1_5",
];
export const MATCH_RESULT_MARKETS = ["home_win", "draw", "away_win", "double_chance_1x", "double_chance_x2", "double_chance_12"];
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
