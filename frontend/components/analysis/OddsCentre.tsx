"use client";

import { Fragment, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useCachedData } from "@/lib/clientCache";
import { HOME_COLOR, fmt, getMatchOddsCentre, pct, type MatchOddsCentre, type OddsRow } from "@/lib/analysis";
import { MARKET_DISPLAY_NAMES } from "@/types";
import { EmptyNote, Panel } from "./StatsKit";

const when = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
// Display order of market families.
const FAMILY: Record<string, string> = {
  match_result: "Resultado 1X2", double_chance_1x: "Doble oportunidad", double_chance_x2: "Doble oportunidad",
  double_chance_12: "Doble oportunidad", over_under_goals: "Goles", btts: "Ambos marcan",
  home_team_total_goals: "Goles del local", away_team_total_goals: "Goles del visitante", corners_total: "Córners",
};
const SELECTION: Record<string, string> = { home: "Local", draw: "Empate", away: "Visitante", over: "Más de", under: "Menos de", yes: "Sí", no: "No" };
const FRESHNESS: Record<string, string> = { vigente: "text-emerald-400", reciente: "text-amber-300", antigua: "text-red-400", sin_fecha: "text-slate-500" };

function rowLabel(row: OddsRow) {
  if (row.model_market && MARKET_DISPLAY_NAMES[row.model_market]) return MARKET_DISPLAY_NAMES[row.model_market];
  return `${SELECTION[row.selection] ?? row.selection}${row.line != null ? ` ${String(row.line).replace(".", ",")}` : ""}`;
}

function History({ row }: { row: OddsRow }) {
  const books = [...new Set(row.series.map(p => p.bookmaker))];
  const [book, setBook] = useState(() => books.sort((a, b) =>
    row.series.filter(p => p.bookmaker === b).length - row.series.filter(p => p.bookmaker === a).length)[0]);
  const points = row.series.filter(p => p.bookmaker === book).map(p => ({ t: Date.parse(p.at), price: p.price }));
  if (!books.length) return <p className="text-xs text-slate-500">Sin historial archivado antes del inicio.</p>;
  return (
    <div>
      <label className="text-xs text-slate-400">Casa{" "}
        <select value={book} onChange={e => setBook(e.target.value)}
                className="rounded border border-surface-border bg-surface-raised px-1.5 py-0.5 text-xs text-slate-200">
          {books.map(b => <option key={b}>{b}</option>)}
        </select>
      </label>
      {points.length < 2 ? <p className="mt-1 text-xs text-slate-500">Un solo precio archivado ({fmt(points[0]?.price)}): sin cambios que dibujar.</p> : (
        <div className="mt-1 h-40">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
              <CartesianGrid stroke="#1f2a44" vertical={false} />
              <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tick={{ fill: "#94a3b8", fontSize: 10 }}
                     tickFormatter={t => when.format(new Date(t))} minTickGap={40} />
              <YAxis domain={["auto", "auto"]} tick={{ fill: "#94a3b8", fontSize: 11 }} width={44} />
              <Tooltip contentStyle={{ background: "#0f172a", borderColor: "#1f2a44", fontSize: 12 }}
                       labelFormatter={t => `Registrado ${when.format(new Date(t as number))}`} formatter={(v: number) => [fmt(v), "Cuota"]} />
              <Line type="stepAfter" dataKey="price" stroke={HOME_COLOR} strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="text-[11px] text-slate-500">Cada punto es un cambio de precio registrado; los tramos planos repiten el último precio conocido.</p>
    </div>
  );
}

export default function OddsCentre({ matchId }: { matchId: number }) {
  const { data, error } = useCachedData<MatchOddsCentre>(`odds-centre:${matchId}`, () => getMatchOddsCentre(matchId));
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <EmptyNote>No se pudieron cargar las cuotas.</EmptyNote>;
  if (!data) return <div className="h-48 animate-pulse rounded-lg bg-surface-alt/40" aria-label="Cargando cuotas" />;
  if (!data.rows.length) return <EmptyNote>API-Football no ha proporcionado cuotas para este partido.</EmptyNote>;
  const started = data.status !== "scheduled";
  const order = Object.keys(FAMILY);
  const groups = new Map<string, OddsRow[]>();
  [...data.rows].sort((a, b) => (order.indexOf(a.market) + 1 || 99) - (order.indexOf(b.market) + 1 || 99))
    .forEach(row => groups.set(FAMILY[row.market] ?? row.market, [...(groups.get(FAMILY[row.market] ?? row.market) ?? []), row]));
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">{data.note}{started && " Los precios mostrados son los últimos prepartido, ya no vigentes."}</p>
      {[...groups.entries()].map(([family, rows]) => (
        <Panel key={family} title={family}>
          <div className="overflow-x-auto">
            <table className="w-full whitespace-nowrap text-sm">
              <thead className="text-xs text-slate-400"><tr>
                <th scope="col" className="px-2 py-1.5 text-left">Selección</th>
                <th scope="col" className="px-2 py-1.5 text-right" title="Mediana de las casas con precio actual">Consenso</th>
                <th scope="col" className="px-2 py-1.5 text-right" title="1 / cuota, con margen">Implícita</th>
                <th scope="col" className="px-2 py-1.5 text-right" title="Normalizada entre todas las selecciones del mercado">Sin margen</th>
                <th scope="col" className="px-2 py-1.5 text-right">Modelo</th>
                <th scope="col" className="px-2 py-1.5 text-right" title="Modelo − probabilidad sin margen">Dif.</th>
                <th scope="col" className="px-2 py-1.5 text-right">Inicial archivada</th>
                <th scope="col" className="px-2 py-1.5 text-right">Casas</th>
              </tr></thead>
              <tbody>
                {rows.map(row => {
                  const key = `${row.market}:${row.line}:${row.selection}`;
                  return (
                    <Fragment key={key}>
                      <tr className="cursor-pointer border-t border-surface-border hover:bg-surface-alt/50" aria-expanded={open === key}
                          onClick={() => setOpen(open === key ? null : key)}>
                        <td className="px-2 py-1.5">{open === key ? "▾" : "▸"} {rowLabel(row)}</td>
                        <td className="px-2 py-1.5 text-right font-semibold tabular-nums">{fmt(row.consensus)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{pct(row.implied)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{pct(row.no_vig)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-violet-300">{pct(row.model_probability)}</td>
                        <td className={`px-2 py-1.5 text-right tabular-nums ${row.model_gap == null ? "" : row.model_gap > 0 ? "text-emerald-400" : "text-slate-400"}`}>
                          {row.model_gap == null ? "—" : `${row.model_gap > 0 ? "+" : ""}${(row.model_gap * 100).toFixed(1)} pp`}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums" title={row.first ? `${row.first.bookmaker} · registrada ${when.format(new Date(row.first.recorded_at))}` : undefined}>
                          {row.first ? fmt(row.first.price) : "—"}</td>
                        <td className="px-2 py-1.5 text-right">{row.bookmakers.length}</td>
                      </tr>
                      {open === key && (
                        <tr><td colSpan={8} className="px-2 pb-3">
                          <div className="grid gap-3 lg:grid-cols-2">
                            <table className="w-full text-xs">
                              <thead className="text-slate-500"><tr>
                                <th scope="col" className="py-1 text-left">Casa</th><th scope="col" className="py-1 text-right">Cuota</th>
                                <th scope="col" className="py-1 text-right" title="Última actualización real del precio según el proveedor">Origen</th>
                                <th scope="col" className="py-1 text-right" title="Cuándo lo descargamos">Descarga</th>
                                <th scope="col" className="py-1 text-right">Estado</th>
                              </tr></thead>
                              <tbody>{row.bookmakers.map(b => (
                                <tr key={b.bookmaker} className="border-t border-surface-border">
                                  <td className="py-1">{b.bookmaker}</td><td className="py-1 text-right tabular-nums">{fmt(b.price)}</td>
                                  <td className="py-1 text-right">{b.origin_at ? when.format(new Date(b.origin_at)) : "—"}</td>
                                  <td className="py-1 text-right">{b.fetched_at ? when.format(new Date(b.fetched_at)) : "—"}</td>
                                  <td className={`py-1 text-right ${FRESHNESS[b.freshness] ?? ""}`}>{started ? "histórica" : b.freshness.replace("_", " ")}</td>
                                </tr>
                              ))}</tbody>
                            </table>
                            <History row={row} />
                          </div>
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      ))}
    </div>
  );
}
