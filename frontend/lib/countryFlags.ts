// Banderas de pais para las 5 ligas del proyecto (ver
// backend/app/normalization/competitions.py::COMPETITIONS -- son las
// unicas 5 que existen, por eso un mapa estatico es correcto aqui y no
// una API por nombre como en team-logo/competition-logo). flagcdn.com
// sirve PNGs estaticos por codigo ISO 3166-1, sin API key ni limite de
// peticiones -- "gb-eng" es el codigo especifico de Inglaterra (distinto
// de "gb", que seria la bandera del Reino Unido completo).
export const COUNTRY_FLAG_CODES: Record<string, string> = {
  Spain: "es",
  England: "gb-eng",
  Germany: "de",
  Italy: "it",
  France: "fr",
};

export function flagUrl(country: string, width: 20 | 40 | 80 = 40): string | null {
  const code = COUNTRY_FLAG_CODES[country];
  if (!code) return null;
  return `https://flagcdn.com/w${width}/${code}.png`;
}
