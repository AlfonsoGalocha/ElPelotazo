"use client";

import { useEffect, useState } from "react";
import { flagUrl } from "@/lib/countryFlags";

// Logo de competicion (TheSportsDB, mismo patron que TeamLogo) + bandera
// del pais (mapa estatico, ver lib/countryFlags.ts -- solo 5 paises
// reales en todo el proyecto). Si el logo de la liga no se encuentra, se
// muestra solo la bandera; si tampoco hay bandera (pais desconocido), solo
// el texto -- nunca un icono generico ni un emoji haciendo de escudo.
const logoCache = new Map<string, string | null>();

function useCompetitionLogo(name: string): string | null {
  const [logoUrl, setLogoUrl] = useState<string | null>(() => logoCache.get(name) ?? null);

  useEffect(() => {
    if (logoCache.has(name)) {
      setLogoUrl(logoCache.get(name) ?? null);
      return;
    }
    let cancelled = false;
    fetch(`/api/competition-logo?name=${encodeURIComponent(name)}`)
      .then((res) => (res.ok ? res.json() : { logoUrl: null }))
      .then((data: { logoUrl: string | null }) => {
        if (cancelled) return;
        logoCache.set(name, data.logoUrl);
        setLogoUrl(data.logoUrl);
      })
      .catch(() => {
        if (!cancelled) {
          logoCache.set(name, null);
          setLogoUrl(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [name]);

  return logoUrl;
}

export default function CompetitionBadge({
  name,
  country,
  className = "",
  showName = true,
}: {
  name: string;
  country?: string;
  className?: string;
  showName?: boolean;
}) {
  const logoUrl = useCompetitionLogo(name);
  const [logoFailed, setLogoFailed] = useState(false);
  const flag = country ? flagUrl(country) : null;

  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      {logoUrl && !logoFailed ? (
        // eslint-disable-next-line @next/next/no-img-element -- hotlink externo con fallback via onError.
        <img
          src={logoUrl}
          alt={name}
          className="h-4 w-4 shrink-0 object-contain"
          onError={() => setLogoFailed(true)}
          loading="lazy"
        />
      ) : flag ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={flag} alt={country} className="h-3 w-4 shrink-0 rounded-[2px] object-cover" loading="lazy" />
      ) : null}
      {showName && <span>{name}</span>}
    </span>
  );
}
