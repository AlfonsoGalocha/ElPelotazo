import type { Competition, Match, Prediction } from "@/types";

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

export function getTopSignals(limit = 20): Promise<Prediction[]> {
  return apiFetch<Prediction[]>(`/predictions/top-signals?limit=${limit}`);
}

export function getBestPredictions(limit = 5, marketFamily?: string): Promise<Prediction[]> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (marketFamily) qs.set("market_family", marketFamily);
  return apiFetch<Prediction[]>(`/predictions/best?${qs.toString()}`);
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
