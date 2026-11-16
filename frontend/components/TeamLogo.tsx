"use client";

import { useEffect, useState } from "react";

// Pedido real del usuario (rediseño, y luego "ya tenemos key de
// API-Football"): escudos REALES por equipo -- nunca emojis, nunca un
// logo generico/inventado. Fuente PREFERIDA: `logoUrl` (el campo
// `Team.logo_url` que ya guarda el backend, capturado directamente de
// API-Football al ingerir cada fixture -- real, estable, sin busqueda por
// nombre). Si no llega (equipo que aun no ha aparecido en ningun fixture
// de API-Football) se cae a `/api/team-logo` (TheSportsDB, busqueda por
// nombre) como red de seguridad. Si NINGUNA de las dos fuentes tiene
// escudo, placeholder elegante: un circulo con las iniciales del equipo,
// color derivado del propio nombre (para que cada equipo tenga SIEMPRE el
// mismo color, sin tabla manual que mantener).
//
// Honestidad: la ruta /api/team-logo (fallback) no se ha podido probar
// contra la red real desde este entorno de desarrollo (egress
// restringido) -- comprobar visualmente una vez desplegado con acceso a
// internet real.
const SIZE_CLASSES: Record<"sm" | "md" | "lg", string> = {
  sm: "h-6 w-6 text-[10px]",
  md: "h-9 w-9 text-xs",
  lg: "h-14 w-14 text-base",
};

// Paleta fija de acentos para los placeholders -- nunca un color random
// distinto en cada render, siempre el mismo equipo = mismo color.
const PLACEHOLDER_HUES = ["#713CFF", "#20D995", "#f59e0b", "#3b82f6", "#ef4444", "#14b8a6", "#ec4899"];

function hashString(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = (hash << 5) - hash + value.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

function initialsFor(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

function InitialsAvatar({ name, size }: { name: string; size: "sm" | "md" | "lg" }) {
  const color = PLACEHOLDER_HUES[hashString(name) % PLACEHOLDER_HUES.length];
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full font-semibold text-white ${SIZE_CLASSES[size]}`}
      style={{ backgroundColor: color }}
      title={name}
    >
      {initialsFor(name)}
    </span>
  );
}

// Cache a nivel de modulo (memoria del navegador, por sesion de pestaña):
// evita re-pedir el mismo escudo cada vez que un componente se vuelve a
// montar en la misma pagina (p.ej. el mismo equipo aparece en varias
// tarjetas del dashboard).
const logoCache = new Map<string, string | null>();

export default function TeamLogo({
  name,
  logoUrl: providedLogoUrl,
  size = "md",
  className = "",
}: {
  name: string;
  // Escudo real ya resuelto por el backend (`Team.logo_url`, via
  // API-Football) -- cuando llega, se usa directamente y nunca se llama a
  // `/api/team-logo`. `null`/`undefined` activan el fallback por nombre.
  logoUrl?: string | null;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const [fallbackLogoUrl, setFallbackLogoUrl] = useState<string | null>(
    () => logoCache.get(name) ?? null
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    if (providedLogoUrl) return; // ya tenemos un escudo real, no hace falta el fallback
    if (logoCache.has(name)) {
      setFallbackLogoUrl(logoCache.get(name) ?? null);
      return;
    }
    let cancelled = false;
    fetch(`/api/team-logo?name=${encodeURIComponent(name)}`)
      .then((res) => (res.ok ? res.json() : { logoUrl: null }))
      .then((data: { logoUrl: string | null }) => {
        if (cancelled) return;
        logoCache.set(name, data.logoUrl);
        setFallbackLogoUrl(data.logoUrl);
      })
      .catch(() => {
        if (!cancelled) {
          logoCache.set(name, null);
          setFallbackLogoUrl(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [name, providedLogoUrl]);

  const resolvedLogoUrl = providedLogoUrl || fallbackLogoUrl;

  if (!resolvedLogoUrl || failed) {
    return <InitialsAvatar name={name} size={size} />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- hotlink externo con fallback via onError; next/image no permite ese swap tan simple.
    <img
      src={resolvedLogoUrl}
      alt={name}
      title={name}
      className={`shrink-0 rounded-full bg-white/5 object-contain ${SIZE_CLASSES[size].split(" ").slice(0, 2).join(" ")} ${className}`}
      onError={() => setFailed(true)}
      loading="lazy"
    />
  );
}
