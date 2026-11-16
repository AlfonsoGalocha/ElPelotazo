import { NextRequest, NextResponse } from "next/server";

// Pedido real del usuario (rediseño): escudos REALES de equipo, nunca
// inventados/genericos, obtenidos automaticamente por nombre (no hay
// team_id de API-Football guardado en nuestra BD, solo canonical_name --
// ver backend/app/db/models/core.py::Team -- asi que la busqueda es por
// nombre, no por id). TheSportsDB tiene un endpoint de busqueda por
// nombre gratuito y sin API key para uso no comercial (key publica "3").
//
// Este route handler, no el componente cliente, hace el fetch: asi el
// cache en memoria de abajo es compartido entre TODOS los visitantes del
// mismo proceso de Next.js (evita pedir el mismo escudo una y otra vez,
// pedido explicito de "cachear para evitar peticiones innecesarias"), y
// se evita cualquier problema de CORS llamando desde el navegador
// directamente a thesportsdb.com.
//
// Honestidad: esta ruta no se ha podido probar contra la red real desde
// el entorno de desarrollo (egress restringido, sin acceso a
// thesportsdb.com) -- escrita siguiendo su documentacion publica. Si el
// nombre no encaja exactamente con el de TheSportsDB, simplemente no
// encuentra el equipo y el frontend cae al placeholder de iniciales
// (nunca un error visible ni una imagen rota).
const THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3";
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24h: un escudo no cambia de un dia para otro

type CacheEntry = { logoUrl: string | null; expiresAt: number };
const cache = new Map<string, CacheEntry>();

function normalizeName(name: string): string {
  return name.trim().toLowerCase();
}

async function lookupLogo(teamName: string): Promise<string | null> {
  const key = normalizeName(teamName);
  const cached = cache.get(key);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.logoUrl;
  }

  let logoUrl: string | null = null;
  try {
    const res = await fetch(`${THESPORTSDB_BASE}/searchteams.php?t=${encodeURIComponent(teamName)}`, {
      // Cache tambien a nivel de fetch de Next.js (capa extra, no
      // sustituye al Map: este fetch cache es por URL exacta, el Map de
      // arriba sobrevive aunque cambie el nombre consultado con
      // variantes de mayusculas/espacios).
      next: { revalidate: 60 * 60 * 24 },
    });
    if (res.ok) {
      const data = await res.json();
      const team = data?.teams?.[0];
      logoUrl = team?.strTeamBadge || team?.strTeamLogo || null;
    }
  } catch {
    // Sin red / timeout / lo que sea: se trata igual que "no encontrado",
    // nunca se propaga un error 500 al frontend por esto.
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
    { headers: { "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400" } }
  );
}
