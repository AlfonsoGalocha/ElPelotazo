export interface Team {
  id: number;
  canonical_name: string;
  // Escudo real (API-Football), `null` si este equipo aun no ha aparecido
  // en ningun fixture de API-Football -- ver TeamLogo.tsx (placeholder
  // elegante en ese caso, nunca una imagen inventada).
  logo_url: string | null;
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
  // "postponed"/"cancelled" anhadidos (Rendimiento del modelo, pedido
  // real del usuario): antes un aplazamiento quedaba indistinguible de
  // "scheduled" para siempre -- ver backend/app/db/models/matches.py.
  status: "scheduled" | "finished" | "postponed" | "cancelled";
  home_goals: number | null;
  away_goals: number | null;
  matchday: number | null;
}

export interface FormMatch {
  match_id: number;
  kickoff_utc: string;
  opponent: string;
  venue: "home" | "away";
  goals_for: number;
  goals_against: number;
  result: "W" | "D" | "L";
}

export interface TeamForm {
  team: Team;
  recent: FormMatch[];
  recent_at_venue: FormMatch[];
}

export interface MatchForm {
  home_team: TeamForm;
  away_team: TeamForm;
  head_to_head: FormMatch[];
}

export interface TeamRecentForm {
  team: Team;
  recent: FormMatch[];
}

export interface H2HBestPrediction {
  id: number;
  market: string;
  line: number | null;
  selection: string;
  model_probability: number;
  market_odds: number | null;
  edge: number | null;
}

export interface ScheduledH2HMatch {
  match_id: number;
  kickoff_utc: string;
  competition: Competition;
  best_prediction: H2HBestPrediction | null;
}

export interface TeamHeadToHead {
  team_a: TeamRecentForm;
  team_b: TeamRecentForm;
  head_to_head: FormMatch[];
  scheduled_match: ScheduledH2HMatch | null;
}

export interface RoundInfo {
  competition_code: string;
  round: number | null;
  round_start: string | null;
  round_end: string | null;
  next_round: number | null;
  is_fallback: boolean;
  is_next_round: boolean;
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
  calibrated_probability: number | null;
  market_probability: number | null;
  market_odds: number | null;
  fair_odds: number;
  edge: number | null;
  expected_value: number | null;
  // EV calculado con el extremo conservador del intervalo de
  // incertidumbre (uncertainty_low), no con model_probability -- nunca
  // mas optimista que expected_value. Ver backend/app/prediction/anomaly.py.
  risk_adjusted_expected_value: number | null;
  // "Mejores oportunidades" (cambio de filosofia del ranking): EV con
  // `calibrated_probability` (`ev_calibrated`) y con la probabilidad
  // conservadora (min(calibrada, uncertainty_low), `ev_conservative`) --
  // distintos de `expected_value`/`risk_adjusted_expected_value`, que
  // usan `model_probability` CRUDA (formula v1/v2). `null` solo sin
  // cuota de mercado real. Ver backend/app/prediction/ranking.py.
  ev_calibrated: number | null;
  ev_conservative: number | null;
  confidence: number;
  data_quality: number;
  uncertainty_low: number | null;
  uncertainty_high: number | null;
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
  // Mision "oportunidades" seccion 2.2 (fiabilidad jerarquica): `null` sin
  // historico suficiente en NINGUN nivel. `source_level` dice de donde
  // sale -- nunca mostrar un nivel "market_tier"/"market_global" como si
  // fuera especifico de esta competicion exacta (ver
  // backend/app/services/evaluation_service.py::TrackRecordEstimate).
  reliability: PredictionReliability | null;
}

export interface PredictionReliability {
  value: number;
  source_level: "competition_market" | "competition_market_shrunk" | "market_tier" | "market_global";
  n_observations: number;
}

export type AnomalyFlag =
  | "CONTRADICCION"
  | "SENAL_BAJA_FIABILIDAD"
  | "OUTLIER"
  | "HIGH_PROBABILITY_LOW_VALUE"
  // Revision "motor 2.0": edge bruto grande con la probabilidad del
  // modelo poco respaldada (intervalo de incertidumbre ancho) -- nunca
  // aparece entre "mejores recomendaciones" aunque el edge sea enorme.
  | "ALTA_INCERTIDUMBRE";

// Fase 4: prediccion de un partido YA FINALIZADO, con el resultado real
// (liquidado por backend/app/services/evaluation_service.py, nunca
// inferido en el frontend).
export interface HistoryPrediction extends Prediction {
  actual_result: string | null;
  is_correct: boolean | null;
  settled_at: string | null;
  // Closing Line Value (ver backend/app/market/clv.py): null hasta que
  // haya historico de cuotas suficiente (nunca retroactivo).
  closing_line_value: number | null;
  // "won"|"lost"|"void"|"half_won"|"half_lost", o null para predicciones
  // pendientes O liquidadas antes de que existiera esta columna -- en
  // ese caso `is_correct` sigue siendo la fuente de verdad (ver
  // backend/app/db/models/modeling.py::PredictionResult.settlement_status).
  settlement_status: string | null;
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

export interface Summary {
  date: string;
  matches_pending: number;
  competitions_count: number;
  opportunities_count: number;
  high_quality_count: number;
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
  // Predicciones de partidos CANCELADOS, liquidadas como "void" (stake
  // devuelto) -- excluidas de model_quality/market_strategy, nunca
  // contadas como "lost" (ver evaluation_service.py::void_cancelled_predictions).
  n_void: number;
  // Bloque 4 de la mision: tasa de acierto real + intervalo de Wilson al
  // 95% + aviso explicito de muestra pequena (n_settled < 30) -- nunca se
  // presenta un punto estimado como preciso sin estos datos al lado.
  accuracy: number;
  accuracy_confidence_interval_95: [number, number];
  low_sample_size: boolean;
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

// Subseccion "Versiones del modelo" (Rendimiento del modelo, pedido real
// del usuario). Ver backend/app/api/routes/models.py::ModelVersionOut.
export interface ModelVersionInfo {
  id: number;
  name: string;
  version: string;
  market_family: string;
  dataset_version: string;
  metrics: Record<string, unknown>;
  training_start: string | null;
  training_end: string | null;
  created_at: string;
  calibration_method: string | null;
  // null = ModelVersion de antes del gate de promocion -- se trata como
  // activa igual que el backend (nunca como "rechazada" por ausencia de dato).
  is_active: boolean | null;
  competition_code: string | null;
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
export const CORNERS_MARKETS = [
  "corners_over_8_5",
  "corners_over_9_5",
  "corners_over_10_5",
  "corners_under_9_5",
];

// Tarjetas se quito del todo (pedido real del usuario): ninguna casa
// agregada por API-Football ofrece ese mercado para estas 5 ligas
// (confirmado contra una respuesta real de /odds), asi que el backend ya
// no genera ninguna prediccion "cards_*" -- ver
// backend/app/prediction/secondary_markets.py.
export function marketFamily(market: string): "goals" | "corners" {
  if (market.startsWith("corners_")) return "corners";
  return "goals";
}

export interface AgentChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AgentToolLogEntry {
  tool: string;
  ok: boolean;
  duration_ms: number;
}

export interface AgentMatchReference {
  match_id: number;
  home_team: string;
  away_team: string;
}

export interface AgentChatResponse {
  reply: string;
  tool_log: AgentToolLogEntry[];
  referenced_matches: AgentMatchReference[];
}
