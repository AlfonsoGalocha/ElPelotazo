// Selecciones nacionales (seccion dedicada "Selecciones", pedido real del
// usuario, 2026-11): lista EXPLICITA de codigos de competicion, igual que
// el backend (normalization/competitions.py::COMPETITIONS) -- nunca
// inferida de `competition.country` (fragil: una futura competicion de
// CLUB tambien podria tener country="Europe", p.ej. la Champions League).
// Alcance decidido explicitamente por el usuario: absoluta MASCULINA
// (Mundial, Eurocopa, Copa America, Copa Africa de Naciones + sus
// clasificatorios, Nations League) -- deja fuera femenino, categorias
// inferiores y amistosos. Mismo set que
// backend/app/ingestion/api_football/client.py::INTERNATIONAL_COMPETITION_EDITIONS.
export const INTERNATIONAL_COMPETITION_CODES = [
  "uefa_nations_league",
  "world_cup",
  "world_cup_qualification_europe",
  "world_cup_qualification_south_america",
  "world_cup_qualification_concacaf",
  "world_cup_qualification_africa",
  "world_cup_qualification_asia",
  "world_cup_qualification_oceania",
  "world_cup_qualification_intercontinental_playoffs",
  "euro_championship",
  "euro_championship_qualification",
  "copa_america",
  "africa_cup_of_nations",
  "africa_cup_of_nations_qualification",
  "concacaf_nations_league",
];

export function isInternationalCompetition(code: string): boolean {
  return INTERNATIONAL_COMPETITION_CODES.includes(code);
}
