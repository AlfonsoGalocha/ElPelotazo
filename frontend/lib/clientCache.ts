"use client";

import { useEffect, useState } from "react";

// Data already loaded survives tab changes (module memory) and reloads (sessionStorage):
// it is shown at once and refreshed in the background, never replaced by a spinner.
const memory = new Map<string, unknown>();
// Requests in flight, so two components asking for the same key share one network call.
const pending = new Map<string, Promise<unknown>>();

function shared<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const existing = pending.get(key) as Promise<T> | undefined;
  if (existing) return existing;
  const request = fetcher().finally(() => pending.delete(key));
  pending.set(key, request);
  return request;
}
const STORAGE_PREFIX = "elpelotazo:cache:";

function readStored<T>(key: string): T | undefined {
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + key);
    return raw ? (JSON.parse(raw) as T) : undefined;
  } catch {
    return undefined;
  }
}

function store(key: string, value: unknown) {
  memory.set(key, value);
  try {
    sessionStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage full or unavailable: the in-memory copy still covers navigation.
  }
}

export function useCachedData<T>(key: string | null, fetcher: () => Promise<T>, options: { refreshMs?: number; delayMs?: number } = {}) {
  const [data, setData] = useState<T | undefined>(() => (key ? (memory.get(key) as T | undefined) : undefined));
  const [error, setError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const { refreshMs, delayMs = 0 } = options;

  useEffect(() => {
    if (!key) return;
    let current = true;
    const cachedValue = (memory.get(key) as T | undefined) ?? readStored<T>(key);
    setData(cachedValue);
    setError(false);
    const load = () => {
      setRefreshing(true);
      shared(key, fetcher)
        .then(value => { if (current) { store(key, value); setData(value); setError(false); } })
        .catch(() => { if (current) setError(true); })
        .finally(() => { if (current) setRefreshing(false); });
    };
    const timeout = setTimeout(load, delayMs);
    const interval = refreshMs ? setInterval(load, refreshMs) : undefined;
    return () => { current = false; clearTimeout(timeout); if (interval) clearInterval(interval); };
    // The key identifies the request; the fetcher closure changes on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, refreshMs, delayMs]);

  return { data, error: error && data === undefined, stale: error && data !== undefined, refreshing };
}
