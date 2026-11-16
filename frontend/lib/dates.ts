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
