import { NextRequest, NextResponse } from "next/server";

// Mismo patron que /api/team-logo (ver ese fichero para el porque):
// busqueda por NOMBRE en TheSportsDB (sin key real, gratuita), cacheada
// en memoria del proceso. Solo 5 competiciones reales en todo el
// proyecto (ver backend/app/normalization/competitions.py), asi que el
// cache practicamente nunca crece mas de 5 entradas.
const THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3";
const CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 dias: un logo de liga cambia muy de vez en cuando

type CacheEntry = { logoUrl: string | null; expiresAt: number };
const cache = new Map<string, CacheEntry>();

async function lookupLogo(leagueName: string): Promise<string | null> {
  const key = leagueName.trim().toLowerCase();
  const cached = cache.get(key);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.logoUrl;
  }

  let logoUrl: string | null = null;
  try {
    const res = await fetch(`${THESPORTSDB_BASE}/searchleagues.php?l=${encodeURIComponent(leagueName)}`, {
      next: { revalidate: 60 * 60 * 24 * 7 },
    });
    if (res.ok) {
      const data = await res.json();
      const league = data?.countrys?.[0] ?? data?.leagues?.[0];
      logoUrl = league?.strBadge || league?.strLogo || null;
    }
  } catch {
    logoUrl = null;
  }

  cache.set(key, { logoUrl, expiresAt: Date.now() + CACHE_TTL_MS });
  return logoUrl;
}

export async function GET(request: NextRequest) {
  const name = request.nextUrl.searchParams.get("name");
  if (!name || name.trim().length === 0) {
    return NextResponse.json({ logoUrl: null }, { status: 400 });
  }
  const logoUrl = await lookupLogo(name);
  return NextResponse.json(
    { logoUrl },
    { headers: { "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800" } }
  );
}
