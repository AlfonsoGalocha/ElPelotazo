"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { searchMatches } from "@/lib/api";
import type { Match } from "@/types";

export default function SearchBar() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Match[]>([]);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      return;
    }
    const timeout = setTimeout(() => {
      searchMatches(query.trim())
        .then((matches) => {
          setResults(matches.slice(0, 8));
          setOpen(true);
        })
        .catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative w-full max-w-sm">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        placeholder="Buscar equipo o partido..."
        className="w-full rounded border border-surface-border bg-surface-raised px-3 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none"
      />
      {open && results.length > 0 && (
        <div className="absolute z-10 mt-1 w-full rounded-lg border border-surface-border bg-surface-raised shadow-lg">
          {results.map((match) => (
            <Link
              key={match.id}
              href={`/matches/${match.id}`}
              onClick={() => setOpen(false)}
              className="flex items-center justify-between border-b border-surface-border px-3 py-2 text-sm last:border-0 hover:bg-black/20"
            >
              <span className="text-slate-200">
                {match.home_team.canonical_name} <span className="text-slate-500">vs</span>{" "}
                {match.away_team.canonical_name}
              </span>
              <span className="text-xs text-slate-500">
                {new Date(match.kickoff_utc).toLocaleDateString("es-ES", { day: "2-digit", month: "short" })}
              </span>
            </Link>
          ))}
        </div>
      )}
      {open && results.length === 0 && query.trim().length >= 2 && (
        <div className="absolute z-10 mt-1 w-full rounded-lg border border-surface-border bg-surface-raised px-3 py-2 text-sm text-slate-500 shadow-lg">
          Sin resultados para &quot;{query}&quot;.
        </div>
      )}
    </div>
  );
}
