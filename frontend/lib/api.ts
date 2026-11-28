import type {
  BestOfDay,
  Competition,
  Match,
  MatchForm,
  MatchHistory,
  ModelPerformanceReport,
  ModelVersionInfo,
  Prediction,
  RoundInfo,
  SelectorDecisionsForMatch,
  SelectorLearningReport,
  SignalDetail,
  Summary,
  Team,
  TeamHeadToHead,
} from "@/types";

// Dos URLs distintas a proposito, no una: en Docker Compose el frontend y
// el backend son contenedores separados, cada uno con su propio
// "localhost". Cuando el codigo de esta funcion corre DENTRO del
// contenedor del frontend (paginas de servidor / Server Components, que
// son la mayoria de esta app), "localhost:8000" apuntaria al propio
// contenedor del frontend, no al backend -- hay que usar el nombre del
// servicio en la red interna de Docker (`API_URL=http://backend:8000` en
// docker-compose.yml). Cuando el codigo corre en el NAVEGADOR (p.ej.
// SearchBar.tsx, marcado "use client"), el unico host que existe es la
// maquina del usuario, asi que ahi si hace falta la URL publica
// (`NEXT_PUBLIC_API_URL`, la que ve `localhost:<puerto-publicado>`).
// `API_URL` (sin el prefijo NEXT_PUBLIC_) nunca se envia al navegador --
// Next.js solo expone al cliente las variables con ese prefijo -- por eso
// es seguro que apunte a un host interno que no existe fuera de Docker.
function resolveApiUrl(): string {
  const browserUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  if (typeof window === "undefined") {
    return process.env.API_URL ?? browserUrl;
  }
  return browserUrl;
}

export async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${resolveApiUrl()}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} respondio ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getCompetitions(): Promise<Competition[]> {
  return apiFetch<Competition[]>("/competitions");
}

// Indicador de conexion de la sidebar (rediseño): un ping real al
// backend, nunca un estado inventado. Se usa solo para saber si
// responde, el cuerpo no importa.
export async function pingHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${resolveApiUrl()}/health`, { cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

// "Resumen de jornada" del dashboard (seccion de rediseño).
export function getSummary(): Promise<Summary> {
  return apiFetch<Summary>("/predictions/summary");
}

export function getTeams(): Promise<Team[]> {
  return apiFetch<Team[]>("/teams");
}

// Pedido real del usuario (pagina principal): elegir dos equipos
// cualquiera y ver su cara a cara + que es mas probable que pase entre
// ellos (solo si hay un partido REAL ya programado, ver backend).
export function getHeadToHead(teamAId: number, teamBId: number): Promise<TeamHeadToHead> {
  return apiFetch<TeamHeadToHead>(`/matches/h2h?team_a_id=${teamAId}&team_b_id=${teamBId}`);
}

export function getUpcomingPredictions(days = 7): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/predictions/today?days=${days}`);
}

export type SignalScope = "current_round" | "next_round" | "all_upcoming";

export interface TopSignalsOptions {
  limit?: number;
  perMatch?: number;
  date?: string;
  sortBy?: "score" | "edge";
  // "current_round" (por defecto): solo la jornada actual de cada
  // competicion (backend/app/services/round_service.py) -- nunca un
  // partido de la jornada siguiente aunque tenga mas edge. "next_round":
  // la siguiente jornada. "all_upcoming": ventana de `days` dias, sin
  // restriccion de jornada (comportamiento anterior).
  scope?: SignalScope;
  days?: number; // solo aplica con scope="all_upcoming"
  competitionCode?: string;
  // Cuota JUSTA del modelo (1/model_probability), no la de mercado --
  // pedido explicito de usuario para acotar por rango (ej. "solo entre 1
  // y 2" = favoritos claros segun el modelo). Independiente del filtro
  // MAX_SIGNAL_ODDS del backend (que limita la cuota de MERCADO, pensado
  // para evitar tiros muy largos, no para segmentar por rango).
  minFairOdds?: number;
  maxFairOdds?: number;
  // Ajustes finos adicionales (seccion 14): nunca sustituyen al filtro
  // duro de calidad del backend, solo lo afinan por request.
  minEdge?: number; // en PUNTOS PORCENTUALES (ej. 5 = 5pp)
  minBookmakers?: number;
  // Expected Value minimo, como fraccion (0.10 = EV +10%) -- pedido real
  // del usuario ("revision motor 2.0", Fase 6/8): un edge grande no es lo
  // mismo que rentabilidad esperada positiva.
  minEv?: number;
  quality?: "HIGH" | "MEDIUM" | "LOW" | "ALL";
}

// "Mejores señales": SOLO predicciones con mercado real valido (ver
// backend/app/prediction/ranking.py), de la JORNADA ACTUAL de cada
// competicion por defecto (`scope="current_round"`) -- nunca "los
// proximos N dias", que puede mezclar jornadas o dejar partidos fuera.
// `sortBy`: "score" (por defecto, ranking compuesto) o "edge" (de mayor a
// menor edge en crudo) -- solo cambia el ORDEN, el filtro de calidad es
// el mismo.
export function getTopSignals(opts: TopSignalsOptions & { allPrices?: boolean; valueOnly?: boolean; opportunityView?: "main" | "high" | "explore" } = {}, available = false): Promise<Prediction[]> {
  const {
    limit = 20,
    date,
    sortBy,
    perMatch,
    scope,
    days,
    competitionCode,
    minFairOdds,
    maxFairOdds,
    minEdge,
    minBookmakers,
    minEv,
    quality,
  } = opts;
  const qs = new URLSearchParams({ limit: String(limit) });
  if (date) qs.set("date", date);
  if (available && opts.opportunityView) qs.set("opportunity_view", opts.opportunityView);
  if (available && opts.valueOnly) qs.set("value_only", "true");
  if (available && opts.allPrices) qs.set("all_prices", "true");
  if (perMatch !== undefined) qs.set("per_match", String(perMatch));
  if (sortBy) qs.set("sort_by", sortBy);
  if (scope) qs.set("scope", scope);
  if (days !== undefined) qs.set("days", String(days));
  if (competitionCode) qs.set("competition_code", competitionCode);
  if (minFairOdds !== undefined) qs.set("min_fair_odds", String(minFairOdds));
  if (maxFairOdds !== undefined) qs.set("max_fair_odds", String(maxFairOdds));
  if (minEdge !== undefined) qs.set("min_edge", String(minEdge));
  if (minBookmakers !== undefined) qs.set("min_bookmakers", String(minBookmakers));
  if (minEv !== undefined) qs.set("min_ev", String(minEv));
  if (quality) qs.set("quality", quality);
  return apiFetch<Prediction[]>(`/predictions/${available ? "available" : "top-signals"}?${qs.toString()}`);
}

// Detalle completo de una senhal: desglose bookmaker-por-bookmaker (con
// diferencia respecto al consenso y si se descarto como outlier) y una
// explicacion en lenguaje llano de por que aparece, siempre trazable a
// los datos reales (nunca "apuesta segura"/"100%").
export function getPredictionDetail(predictionId: number): Promise<SignalDetail> {
  return apiFetch<SignalDetail>(`/predictions/${predictionId}/detail`);
}

// Explicacion de la seleccion (v2.8.3): 404 cuando el scheduler todavia
// no ha capturado ningun corte del selector para ese partido (fuera de
// su ventana de 48h, o partido muy reciente) -- el llamador debe tratarlo
// como "no disponible", nunca como un error que oculte el resto de la
// pagina.
export function getSelectorDecisionsForMatch(matchId: number): Promise<SelectorDecisionsForMatch> {
  return apiFetch<SelectorDecisionsForMatch>(`/predictions/selector-decisions/${matchId}`);
}

export function getBestPredictions(limit = 5, marketFamily?: string): Promise<Prediction[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (marketFamily) qs.set("market_family", marketFamily);
  return apiFetch<Prediction[]>(`/predictions/best?${qs.toString()}`);
}

// Predicciones del modelo SIN mercado (corners siempre en el dataset
// historico; goles sin cuota todavia). Existen y son validas, pero nunca
// compiten en el ranking de "mejores señales" — se muestran aparte,
// etiquetadas.
export interface OpportunityDiagnostics {
  counts: {
    matches: number;
    matches_with_market: number;
    matches_with_available_price: number;
    matches_with_preferred_price: number;
    candidates_with_available_price: number;
    candidates_with_preferred_price: number;
    candidates_with_valid_market: number;
    positive_estimated_ev: number;
    passed_gate: number;
    opportunities: number;
    observation: number;
    candidates: number;
    candidates_in_band: number;
    usable_probabilities: number;
    high_confidence: number;
  };
  reasons: Record<string, number>;
  first_reasons: Record<string, number>;
  by_market?: Record<string, { qualified: number; high: number; confidence_reasons: Record<string, number> }>;
  min_odds: number;
  max_odds: number;
}

export function getOpportunityDiagnostics(filters: Record<string, string | number | undefined>) {
  const qs = new URLSearchParams({ summary_only: "true", execution: "true" });
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined) qs.set(key, String(value));
  }
  return apiFetch<OpportunityDiagnostics>(`/predictions/best/debug?${qs.toString()}`);
}

export function getModelOnlyPredictions(limit = 20): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/predictions/model-only?limit=${limit}`);
}

export function getMatch(matchId: number): Promise<Match> {
  return apiFetch<Match>(`/matches/${matchId}`);
}

export function getMatchPredictions(matchId: number): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/matches/${matchId}/predictions`);
}

export function getMatchForm(matchId: number, limit = 5): Promise<MatchForm> {
  return apiFetch<MatchForm>(`/matches/${matchId}/form?limit=${limit}`);
}

export function searchMatches(query: string): Promise<Match[]> {
  return apiFetch<Match[]>(`/matches?search=${encodeURIComponent(query)}&status=scheduled`);
}

export interface MatchesListFilters {
  competitionScope?: "primary" | "experimental" | "all";
  competitionCode?: string;
  dateFrom?: string; // YYYY-MM-DD
  dateTo?: string;
  search?: string;
  status?: "scheduled" | "finished";
  limit?: number;
}

// Listado general de partidos (pagina "Partidos" del menu): reutiliza
// GET /matches tal cual (ya soportaba estos filtros, solo faltaba una
// pagina de frontend que los expusiera todos juntos).
export function getMatchesList(filters: MatchesListFilters = {}): Promise<Match[]> {
  const qs = new URLSearchParams();
  if (filters.competitionScope) qs.set("competition_scope", filters.competitionScope);
  if (filters.competitionCode) qs.set("competition_code", filters.competitionCode);
  if (filters.dateFrom) qs.set("date_from", filters.dateFrom);
  if (filters.dateTo) qs.set("date_to", filters.dateTo);
  if (filters.search) qs.set("search", filters.search);
  if (filters.status) qs.set("status", filters.status);
  qs.set("limit", String(filters.limit ?? 200));
  return apiFetch<Match[]>(`/matches?${qs.toString()}`);
}

export interface CurrentRoundPredictions {
  round: RoundInfo | null;
  predictions: Prediction[];
}

// Predicciones de la JORNADA ACTUAL de una competicion (no "los proximos
// partidos que haya" sin mas, ver services/round_service.py). Si la
// competicion no tiene partidos programados, `round` viene `null` y
// `predictions` vacio (caso normal, no un error).
export function getCurrentRoundPredictions(competitionCode: string): Promise<CurrentRoundPredictions> {
  return apiFetch<CurrentRoundPredictions>(`/predictions/current-round?competition_code=${competitionCode}`);
}

// Fase 3 (seccion 16): "mejor señal del dia", filtro MAS estricto del
// sistema. `prediction` es `null` cuando ninguna candidata cumple TODOS
// los requisitos minimos -- nunca se rebaja el filtro para forzar una.
export function getBestOfDay(competitionCode?: string): Promise<BestOfDay> {
  const qs = new URLSearchParams();
  if (competitionCode) qs.set("competition_code", competitionCode);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<BestOfDay>(`/predictions/best-of-day${suffix}`);
}

// Fase 4: partidos FINALIZADOS (Match.status, nunca por fecha).
export function getFinishedFixtures(competitionCode?: string, limit = 50): Promise<Match[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (competitionCode) qs.set("competition_code", competitionCode);
  return apiFetch<Match[]>(`/matches/fixtures/finished?${qs.toString()}`);
}

// Fase 4: "Historico" -- partidos finalizados con TODAS sus predicciones
// y el resultado real (liquidado por el backend, nunca inferido aqui).
export function getHistory(competitionCode?: string, limit = 30, withPredictions = false): Promise<MatchHistory[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (competitionCode) qs.set("competition_code", competitionCode);
  if (withPredictions) qs.set("with_predictions", "true");
  return apiFetch<MatchHistory[]>(`/history?${qs.toString()}`);
}

export function getHistoryDetail(matchId: number): Promise<MatchHistory> {
  return apiFetch<MatchHistory>(`/history/${matchId}`);
}

// Fase 5: rendimiento REAL del modelo (solo predicciones ya liquidadas),
// segmentado por competicion/mercado/version de modelo.
export function getModelPerformance(competitionCode?: string, market?: string): Promise<ModelPerformanceReport> {
  const qs = new URLSearchParams();
  if (competitionCode) qs.set("competition_code", competitionCode);
  if (market) qs.set("market", market);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<ModelPerformanceReport>(`/models/performance${suffix}`);
}

// Subseccion "Versiones del modelo" (Rendimiento del modelo, pedido real
// del usuario): listado completo de ModelVersion, mas reciente primero
// (ver backend/app/api/routes/models.py) -- incluye las NO activas
// (nunca se borran), para poder comparar version a version.
export function getModels(): Promise<ModelVersionInfo[]> {
  return apiFetch<ModelVersionInfo[]>("/models");
}

// Fase E (revision v2.8, seccion 10 del brief): aprendizaje del selector
// sobre sus propias decisiones -- resumen de
// backend/app/services/selector_decision_service.py::evaluate_selector_decisions.
// Puramente prospectivo (sin backfill, ver docstring de
// SelectorDecisionSnapshot en el backend): puede tardar dias/semanas en
// tener suficientes partidos liquidados para que las tasas de acierto
// signifiquen algo.
export function getSelectorDecisionsReport(): Promise<SelectorLearningReport> {
  return apiFetch<SelectorLearningReport>("/predictions/selector-decisions");
}

export interface DataSourcesStatus {
  football_data_co_uk_enabled: boolean;
  api_football_enabled: boolean;
  odds_api_enabled: boolean;
  odds_api_fetch_additional_markets: boolean;
}

// Pagina "Configuracion": solo booleanos de que fuentes estan activas,
// nunca una key real (ver backend/app/api/routes/health.py).
export function getDataSourcesStatus(): Promise<DataSourcesStatus> {
  return apiFetch<DataSourcesStatus>("/config/data-sources");
}

// Panel tecnico de diagnostico de cuotas (seccion "Configuracion", pedido
// real del usuario tras investigar por que la app mostraba cuotas
// desactualizadas): el ULTIMO intento real de cada
// (provider, operation, competicion) -- exito o fallo -- mas la frescura
// ya guardada en MatchOdds. Nunca inventado: `fixtures_fetched`/`matched`
// quedan `null` cuando el intento fallo antes de contar nada.
export interface OddsIngestionRun {
  provider: string;
  operation: string;
  competition_code: string;
  started_at: string;
  finished_at: string;
  fixtures_fetched: number | null;
  matched: number | null;
  error: string | null;
}

export interface OddsDiagnostics {
  latest_runs: OddsIngestionRun[];
  most_recent_odds_recorded_at: string | null;
  bookmakers_seen: string[];
  scheduled_matches_with_odds: number;
}

export function getOddsDiagnostics(): Promise<OddsDiagnostics> {
  return apiFetch<OddsDiagnostics>("/config/odds-diagnostics");
}

// "Mejores oportunidades" (cambio de filosofia del ranking, seccion 7 del
// pedido): primera preferencia de usuario editable en RUNTIME de esta
// app (ver backend/app/services/ranking_config_service.py) -- el resto
// de "Configuracion" es de solo lectura. `default_*` son los valores de
// fabrica (`Settings`), para poder mostrar "restaurar por defecto" sin
// hardcodearlos aqui tambien.
export interface RankingConfig {
  min_opportunity_odds: number;
  max_opportunity_odds: number;
  default_min_opportunity_odds: number;
  default_max_opportunity_odds: number;
}

export function getRankingConfig(): Promise<RankingConfig> {
  return apiFetch<RankingConfig>("/config/ranking");
}

// Lanza con el mensaje real del backend (422: "max_odds debe ser mayor
// que min_odds", etc.) si la banda no es valida -- nunca se valida solo
// en el cliente, el backend es la unica fuente de verdad.
export async function updateRankingConfig(minOdds: number, maxOdds: number): Promise<RankingConfig> {
  const res = await fetch(`${resolveApiUrl()}/config/ranking`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ min_opportunity_odds: minOdds, max_opportunity_odds: maxOdds }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `API /config/ranking respondio ${res.status}`);
  }
  return res.json() as Promise<RankingConfig>;
}

export type PublicationSelection = {
  match_id: number; match: string; kickoff: string; market: string; competition: string;
  model_version_id: number; probability: number; calibrated: number | null;
  odds: number | null; status: string; captured_at: string; family?: string;
  probability_error?: number | null;
  profit_units?: number; cumulative_units?: number; matchday?: number | null;
  score?: [number | null, number | null];
  alternatives?: { market: string; probability: number; calibrated: number | null; odds: number | null }[];
};

export type PublicationReport = {
  metrics: {family: string; n: number; wins: number; losses: number; brier: number;
    log_loss: number; ece: number; segments: {competition: string; model_version: number; n: number; brier: number; log_loss: number}[]; calibration: {low: number; high: number; n: number;
      predicted: number; observed: number}[]}[];
  history: PublicationSelection[];
  recommendations: PublicationSelection[];
  breakdown: Record<string, {label: string; n: number; wins: number; roi: number}[]>;
  performance: {n: number; settled: number; wins: number; losses: number; void: number;
    profit_units: number; roi: number | null; drawdown_units: number;};
};

export function getPublications(filters: Record<string, string | undefined> = {}) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) qs.set(key, value);
  return apiFetch<PublicationReport>(`/publications?${qs}`);
}

export function getPublication(matchId: number) {
  return apiFetch<{
    captured_at: string;
    ranking_version: string;
    groups: Record<string, { principal: string; alternatives: PublicationSelection[] }>;
    recommendation: PublicationSelection | null;
    ranking: (PublicationSelection & { reasons?: string[]; estimated_ev?: number | null })[];
    feature_snapshots: { id: number; is_home: boolean; as_of: string; features: Record<string, unknown> }[];
  }>(`/publications/${matchId}`);
}

export interface RoundAccuracy {
  season: string;
  kickoff: string;
  flat_stake: { staked_cents: number; returned_cents: number };
  competition: string;
  name: string;
  matchday: number | null;
  round_name?: string | null;
  fixtures: number;
  model: { wins: number; losses: number; without_forecast: number; awaiting_settlement: number };
  wins: number;
  losses: number;
  pending: number;
  without_forecast: number;
  awaiting_settlement: number;
  cancelled: number;
}

export function getRoundAccuracy(allRounds = false) {
  return apiFetch<RoundAccuracy[]>(`/publications/round-accuracy${allRounds ? "?all_rounds=true" : ""}`);
}

export function getPublicationCoverage() {
  return apiFetch<{
    training_corpus: { first_season: string; last_season: string; finished_matches: number;
      matches_with_corners: number; matches_with_closing_prices: number;
      matches_with_closing_result: number; matches_with_closing_goals_2_5: number;
      matches_with_closing_corners: number; };
    historical_liquidated: number;
    historical_before_kickoff: number;
    historical_after_kickoff: number;
    official_captures: number;
    official_finished: number;
  }>("/publications/coverage");
}

type BotTeamForm = { matches: number; wins: number; draws: number; losses: number };

export type BotTicketKind = "auto_single" | "auto_double" | "manual_prematch" | "manual_live";

export type BotPerformance = {
  tickets: number; settled: number; pending: number; won: number; lost: number; void: number;
  staked_cents: number; profit_cents: number; roi: number | null; hit_rate: number | null;
};

export type BotLeg = {
  match_id: number;
  match: string;
  market: string;
  competition?: string;
  kickoff?: string;
  probability?: number;
  probability_source?: "original" | "calibrated";
  effective_probability?: number | null;
  confidence?: number;
  confidence_components?: Record<string, number>;
  ev?: number | null;
  price: number;
  bookmaker: string;
  live?: { home: number; away: number; minute: number | null; extra: number | null; status: string; goal_met: boolean };
};

export type BotTicket = {
  stake_cents: number;
  returned_cents: number;
  state: "won" | "lost" | "void" | "pending";
  kind: BotTicketKind;
  strategy?: "ventaja_confirmada" | "alta_probabilidad";
  leg_states: ("won" | "lost" | "void" | "pending")[];
  legs: BotLeg[];
  price?: number;
  probability?: number;
  effective_probability?: number;
  confidence?: number;
  ev?: number;
  effective_ev?: number;
  bankroll_share?: number;
  max_loss_cents?: number;
  reason?: string;
  double?: { sensitivity: { scenario: string; rho: number; probability: number; ev: number }[]; robust_ev: number; synthetic_price: boolean };
};

export type BotAlternative = {
  description: string; feasible: boolean; chosen: boolean; verdict: string;
  stakes_cents?: number[]; committed_cents?: number; committed_share?: number;
  expected_profit_cents?: number; expected_log_growth?: number; robust_log_growth?: number;
  loss_probability?: number; volatility_cents?: number; max_loss_cents?: number; max_loss_share?: number;
  scenarios?: { outcome: Record<string, boolean>; probability: number; profit_cents: number }[];
  binding_limits?: string[];
};

export type BotRiskProfile = {
  name: string; label: string; kelly_fraction: number; max_single_fraction: number; max_single_cents: number;
  max_double_fraction: number; max_exposure_fraction: number; min_reserve_fraction: number;
  max_scenario_loss_fraction: number; experimental: boolean; max_single_at_equity_cents: number;
};

export type BotBankroll = {
  initial_cents: number;
  balance_cents: number;
  reserved_cents: number;
  kind_labels: Record<BotTicketKind, string>;
  summary: {
    available_cents: number; committed_cents: number; equity_cents: number; net_profit_cents: number;
    peak_equity_cents: number; max_drawdown: number; current_drawdown: number;
    all: BotPerformance; automatic: BotPerformance; manual: BotPerformance;
    by_kind: Record<BotTicketKind, BotPerformance>;
  };
  learning: { note: string; min_settled_for_adjustment: number } & Record<
    "by_kind" | "by_strategy" | "by_staking" | "by_odds" | "by_probability" | "by_market" | "by_competition" | "by_confidence",
    Record<string, BotPerformance>
  >;
  shadow: { cuts: number; note: string; banks: Record<string, { balance_cents: number; tickets: number; pending: number; profit_cents: number }> };
  risk: { active: string; note: string | null; authorized_experimental: boolean; profiles: Record<string, BotRiskProfile> };
  days: {
    day: string;
    captured_at: string;
    policy_version?: string;
    odds_band: [number, number];
    profit_cents: number;
    fixtures: {
      match_id: number;
      match: string;
      assessed: number;
      eligible: number;
      decision?: "goles" | "ambos_marcan" | "doble_oportunidad" | "equipo_no_favorito_marca" | "pasar";
      context?: {
        home_form: BotTeamForm;
        away_form: BotTeamForm;
        home_standing: { position: number } | null;
        away_standing: { position: number } | null;
        h2h: { matches: number; both_scored: number; over_2_5: number };
        favorite: "home" | "away" | null;
      };
      combo_rejected?: { market: string; price: number; probability: number; ev: number | null; reasons: string[] }[];
    }[];
    capital?: {
      policy_version: string;
      profile: BotRiskProfile;
      profile_note: string | null;
      equity_cents: number;
      chosen: string;
      reason: string;
      multiplier: number;
      evaluated_alternatives: number;
      alternatives: BotAlternative[];
      rejected_doubles: { matches: string[]; markets: string[]; price: number; reasons: string[]; model_probability: number }[];
      accepted_doubles: { matches: string[]; markets: string[]; price: number; effective_probability: number; robust_ev: number }[];
    };
    tickets: BotTicket[];
  }[];
};

export type BotSimulations = {
  paths: number; bets: number; initial_cents: number; assumptions: string;
  worlds: Record<string, Record<string, {
    median_final_cents: number; p5_final_cents: number; p95_final_cents: number; loss_probability: number;
    halved_probability: number; median_max_drawdown: number; p95_max_drawdown: number;
    median_stake_cents: number; p99_stake_cents: number; share_stakes_100_plus: number; bets_placed_share: number;
  }>>;
};

export function getBotBankroll(trial = false, live = false) {
  return apiFetch<BotBankroll>(`/publications/bot-bankroll?trial=${trial}&live=${live}`);
}

export type BotPlanStep = {
  cut_at: string;
  fixtures: { match_id: number; match: string; kickoff: string; eligible?: number; assessed?: number;
    single?: { market: string; price: number; probability: number; effective_probability: number | null; confidence: number; confidence_gates: string[] } | null;
    combo_rejected?: { market: string; price: number; probability: number; ev: number | null; reasons: string[] }[] }[];
  lookahead?: number[];
  chosen: string;
  reason: string;
  tickets: BotTicket[];
  alternatives: BotAlternative[];
  evaluated_alternatives?: number;
  capital_before: { available: number; reserved: number };
  capital_after: { available: number; reserved: number };
};

export type BotPlan = {
  generated_at: string; day: string; available_cents: number; reserved_cents: number; note: string;
  cache_age_seconds: number;
  next: BotPlanStep | null;
  steps: BotPlanStep[];
  executed: { captured_at: string; policy_version?: string; chosen: string | null; reason: string | null;
    fixtures: { match_id: number; match: string }[]; tickets: BotTicket[] }[];
};

export type GradedPick = { market: string; probability: number; odds: number | null; is_correct: boolean | null;
  bookmaker?: string; ev?: number | null };

export type PreMatchForecast = {
  source: "previsión_prepartido" | "captura_laboratorio";
  captured_at: string;
  forecast: GradedPick | null;
  opportunity: GradedPick | null;
  markets: GradedPick[];
};

export function getPreMatchForecast(matchId: number) {
  return apiFetch<PreMatchForecast>(`/history/${matchId}/pre-match`);
}

export function getBotPlan() {
  return apiFetch<BotPlan>("/publications/bot-plan");
}

export function getBotSimulations() {
  return apiFetch<BotSimulations>("/publications/bot-bankroll/simulations");
}

export type ShadowEconomics = {
  selections: number; settled: number; wins: number; losses: number; void: number;
  accuracy: number | null; mean_odds: number | null; mean_probability: number | null;
  mean_ev: number | null; profit_cents: number; roi: number | null; drawdown_cents: number;
  roi_interval: [number, number] | null;
  curve: { at: string; profit_cents: number }[];
};

export type ShadowReport = {
  mode: "prospective_shadow";
  fixtures: number;
  band: string;
  bands: Record<string, [number, number]>;
  strategies: Record<string, ShadowEconomics & {
    common_quality: { n: number; brier: number | null; log_loss: number | null; brier_interval: [number, number] | null };
    coverage: number | null;
    by_scope: Record<string, ShadowEconomics>;
    by_league: Record<string, ShadowEconomics & { fixtures: number; coverage: number;
      common_quality: { brier: number | null; log_loss: number | null } }>;
    by_market: Record<string, ShadowEconomics>;
    by_odds: Record<string, ShadowEconomics>;
  }>;
  history: {
    match_id: number; match: string; capture: string; kickoff?: string; league?: string;
    decision_reasons: Record<string, string | null>;
    decisions: Record<string, {
      market: string; price: number; bookmaker: string; probability: number;
      outcome: string; corrected_source: string; corrected_model_version_id?: number; reference: { houses: number } | null;
    } | null>;
  }[];
};

export type ShadowFilters = { scope?: string; market?: string; strategy?: string; date_from?: string; date_to?: string };

export function getShadowReport(band = "principal", competition?: string, filters: ShadowFilters = {}) {
  const params = new URLSearchParams({ band });
  if (competition) params.set("competition", competition);
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  return apiFetch<ShadowReport>(`/publications/strategies?${params}`);
}

type ShadowOperationalFixture = {
  match_id: number; match: string; league: string; kickoff: string; window_opens: string;
  reason?: string; captured_at?: string; selections?: number; match_status?: string;
};

export type ShadowOperations = {
  status: string; scheduler_active: boolean; last_scan: string | null; last_success: string | null;
  last_odds_update: string | null; latest_quote_origin: string | null; latest_quote_query: string | null;
  next_capture: ShadowOperationalFixture | null; upcoming: ShadowOperationalFixture[];
  missed: ShadowOperationalFixture[]; captured: ShadowOperationalFixture[]; pending_selections: number;
  errors: { at: string; type: string }[];
  provider_errors: { at: string; league: string; provider: string }[];
  last_prediction_model: number | null;
  calibrations: { id: number; dataset: string; reason: string }[];
};

export function getShadowOperations(scope: "primary" | "experimental" = "primary") {
  return apiFetch<ShadowOperations>(`/publications/strategies/operations?scope=${scope}`);
}

export type MatchPreview = {
  model_version_id: number;
  evaluated_at: string;
  markets: {
    market: string;
    original_probability: number;
    calibrated_probability: number | null;
    market_probability: number | null;
    odds: number | null;
    odds_origin_at: string | null;
  }[];
};

export function getMatchPreview(id: number) {
  return apiFetch<MatchPreview | null>(`/matches/${id}/preview`);
}

export type ChampionsPreviewPick = { market: string; probability: number; odds: number; model_version_id: number; bookmaker?: string; probability_source?: "original" | "calibrated" };

export function getChampionsPreviewPicks(policy: "verified" | "legacy" = "verified") {
  return apiFetch<Record<string, ChampionsPreviewPick>>(`/matches/champions/previews?policy=${policy}`);
}

export type TodayForecast = {
  odds: number;
  bookmaker: string;
  quote_status: "current" | "stale" | "unverified";
  ev: number | null;
  strict_reasons: string[];
  market: string;
  probability: number;
  probability_source: "original" | "calibrated";
  model_version_id: number;
  generated_at: string;
  observations: number | null;
  interval: [number, number] | null;
  limited_evidence: boolean;
};

export type TodayPreview = {
  experimental_forecast?: { market: string; probability: number } | null;
  forecast: TodayForecast | null;
  forecast_unavailable_reason: string | null;
  opportunity: (ChampionsPreviewPick & { ev: number; quote_age_minutes: number }) | null;
  counts: Record<string, number>;
  markets: Record<string, {
    reasons: string[]; confidence_reasons: string[]; quote_origin_at: string | null; quote_fetched_at: string | null;
  }>;
  sensitivity: Record<string, { market: string } | null>;
};

export function getTodayPreviews(ids: number[]) {
  const params = new URLSearchParams();
  ids.forEach(id => params.append("match_ids", String(id)));
  return apiFetch<Record<string, TodayPreview>>(`/matches/today/previews?${params}`);
}

export function getModelLearning() {
  type Scores = { brier: number; log_loss: number };
  return apiFetch<{
    competition: string;
    name: string;
    titular: { id: number; certification: string } | null;
    candidate: { id: number; trained_at: string; last_round: string | null; new_fit_matches: number | null } | null;
    comparison_status: "no_titular" | "insufficient_sample" | "paired_evidence_available";
    metrics: Record<string, { matches: number; titular: Scores; candidate: Scores }>;
    history: { id: number; created_at: string; state: "in_service" | "inactive" | "legacy" }[];
  }[]>("/publications/learning");
}


export type CompetitionResearch = {
  code: string; name: string; country: string; phase: number; api_id: number;
  data_status: string; model_status: string; tracking_status: string; evaluation_status: string;
  seasons: { season: string; fixtures: number; finished: number; goals: number; corners: number }[];
  model: { id: number; version: string; validation: {
    fit_before: string; evaluation_matches: number; corner_matches: number;
    metrics: Record<string, { n: number; original: { brier: number; log_loss: number; ece: number };
      calibrated?: { brier: number; log_loss: number; ece: number } }>;
  } | null } | null;
  quotes: number; captures: number; settled_captures: number;
};

export function getCompetitionResearch() {
  return apiFetch<CompetitionResearch[]>("/competitions/research/status");
}
