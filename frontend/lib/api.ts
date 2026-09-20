import type {
  BestOfDay,
  Competition,
  Match,
  MatchHistory,
  ModelPerformanceReport,
  Prediction,
  RoundInfo,
  SignalDetail,
} from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} respondio ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getCompetitions(): Promise<Competition[]> {
  return apiFetch<Competition[]>("/competitions");
}

export function getUpcomingPredictions(days = 7): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/predictions/today?days=${days}`);
}

export type SignalScope = "current_round" | "next_round" | "all_upcoming";

export interface TopSignalsOptions {
  limit?: number;
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
  quality?: "HIGH" | "MEDIUM" | "LOW" | "ALL";
}

// "Mejores señales": SOLO predicciones con mercado real valido (ver
// backend/app/prediction/ranking.py), de la JORNADA ACTUAL de cada
// competicion por defecto (`scope="current_round"`) -- nunca "los
// proximos N dias", que puede mezclar jornadas o dejar partidos fuera.
// `sortBy`: "score" (por defecto, ranking compuesto) o "edge" (de mayor a
// menor edge en crudo) -- solo cambia el ORDEN, el filtro de calidad es
// el mismo.
export function getTopSignals(opts: TopSignalsOptions = {}): Promise<Prediction[]> {
  const {
    limit = 20,
    date,
    sortBy,
    scope,
    days,
    competitionCode,
    minFairOdds,
    maxFairOdds,
    minEdge,
    minBookmakers,
    quality,
  } = opts;
  const qs = new URLSearchParams({ limit: String(limit) });
  if (date) qs.set("date", date);
  if (sortBy) qs.set("sort_by", sortBy);
  if (scope) qs.set("scope", scope);
  if (days !== undefined) qs.set("days", String(days));
  if (competitionCode) qs.set("competition_code", competitionCode);
  if (minFairOdds !== undefined) qs.set("min_fair_odds", String(minFairOdds));
  if (maxFairOdds !== undefined) qs.set("max_fair_odds", String(maxFairOdds));
  if (minEdge !== undefined) qs.set("min_edge", String(minEdge));
  if (minBookmakers !== undefined) qs.set("min_bookmakers", String(minBookmakers));
  if (quality) qs.set("quality", quality);
  return apiFetch<Prediction[]>(`/predictions/top-signals?${qs.toString()}`);
}

// Detalle completo de una senhal: desglose bookmaker-por-bookmaker (con
// diferencia respecto al consenso y si se descarto como outlier) y una
// explicacion en lenguaje llano de por que aparece, siempre trazable a
// los datos reales (nunca "apuesta segura"/"100%").
export function getPredictionDetail(predictionId: number): Promise<SignalDetail> {
  return apiFetch<SignalDetail>(`/predictions/${predictionId}/detail`);
}

export function getBestPredictions(limit = 5, marketFamily?: string): Promise<Prediction[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (marketFamily) qs.set("market_family", marketFamily);
  return apiFetch<Prediction[]>(`/predictions/best?${qs.toString()}`);
}

// Predicciones del modelo SIN mercado (tarjetas/corners siempre; goles sin
// cuota todavia). Existen y son validas, pero nunca compiten en el
// ranking de "mejores señales" — se muestran aparte, etiquetadas.
export function getModelOnlyPredictions(limit = 20): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/predictions/model-only?limit=${limit}`);
}

export function getMatch(matchId: number): Promise<Match> {
  return apiFetch<Match>(`/matches/${matchId}`);
}

export function getMatchPredictions(matchId: number): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/matches/${matchId}/predictions`);
}

export function searchMatches(query: string): Promise<Match[]> {
  return apiFetch<Match[]>(`/matches?search=${encodeURIComponent(query)}&status=scheduled`);
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
export function getHistory(competitionCode?: string, limit = 30): Promise<MatchHistory[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (competitionCode) qs.set("competition_code", competitionCode);
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
