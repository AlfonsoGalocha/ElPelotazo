// Selecciones nacionales (seccion dedicada "Selecciones", pedido real del
// usuario, 2026-11): lista EXPLICITA de codigos de competicion, igual que
// el backend (normalization/competitions.py::COMPETITIONS) -- nunca
// inferida de `competition.country` (fragil: una futura competicion de
// CLUB tambien podria tener country="Europe", p.ej. la Champions League).
export const INTERNATIONAL_COMPETITION_CODES = ["uefa_nations_league"];

export function isInternationalCompetition(code: string): boolean {
  return INTERNATIONAL_COMPETITION_CODES.includes(code);
}
