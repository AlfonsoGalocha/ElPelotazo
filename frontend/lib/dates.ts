// Bug real reportado por el usuario: las paginas que son Server
// Components de Next.js formatean fechas corriendo en el SERVIDOR
// (dentro del contenedor Docker, normalmente en UTC), mientras que las
// que son "use client" (p.ej. /matches) la formatean en el navegador del
// usuario (su zona horaria real) -- el MISMO partido salia a horas
// distintas segun en que pagina se mirara (Dashboard: 20:00, /matches:
// 21:00, la hora correcta en España). La API ya manda el instante real
// en UTC con offset explicito (ver backend/app/schemas/match.py::
// UtcDatetime) -- lo que faltaba era fijar SIEMPRE la misma zona horaria
// al convertirlo a "hora de pared", sin depender de donde corre el
// codigo. Esta app es solo para las 5 grandes ligas europeas con
// interfaz en español: se fija a Europe/Madrid pase lo que pase.
const TIME_ZONE = "Europe/Madrid";

export function formatMatchDateTime(iso: string, options?: Intl.DateTimeFormatOptions): string {
  return new Date(iso).toLocaleString("es-ES", { ...options, timeZone: TIME_ZONE });
}

export function formatMatchDateOnly(iso: string, options?: Intl.DateTimeFormatOptions): string {
  return new Date(iso).toLocaleDateString("es-ES", { ...options, timeZone: TIME_ZONE });
}

export function formatMatchTimeOnly(iso: string, options?: Intl.DateTimeFormatOptions): string {
  return new Date(iso).toLocaleTimeString("es-ES", { ...options, timeZone: TIME_ZONE });
}

// Misma regla que backend/app/utils/dates.py::season_label (julio es el
// mes de corte) -- pedido real del usuario, subsección "Jornadas" de
// "Rendimiento del modelo": selector de temporada. El backend no expone
// `season_label` en `MatchOut` (anhadirlo tocaria muchos endpoints para
// un solo selector), asi que se deriva aqui de `kickoff_utc`, que ya
// viaja en todas las respuestas. Para selecciones (sin concepto limpio
// de "temporada de dos anhos") esto da simplemente el anho de corte
// real, que sigue siendo una forma razonable de agrupar por "edicion".
export function seasonLabelFor(iso: string): string {
  const date = new Date(iso);
  const month = date.getUTCMonth() + 1; // 1-12
  const startYear = month >= 7 ? date.getUTCFullYear() : date.getUTCFullYear() - 1;
  return `${startYear}/${String((startYear + 1) % 100).padStart(2, "0")}`;
}
