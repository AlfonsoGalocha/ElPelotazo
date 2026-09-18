import type { Competition, Match, Prediction, RoundInfo } from "@/types";

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

// "Mejores señales": SOLO predicciones con mercado real valido (ver
// backend/app/prediction/ranking.py). Nunca incluye tarjetas/corners ni
// goles sin cuota todavia. `date` (YYYY-MM-DD) filtra a un dia concreto en
// vez de la ventana relativa por defecto (proximos 4 dias).
export function getTopSignals(limit = 20, date?: string): Promise<Prediction[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (date) qs.set("date", date);
  return apiFetch<Prediction[]>(`/predictions/top-signals?${qs.toString()}`);
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
