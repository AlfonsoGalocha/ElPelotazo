"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BotBankroll } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";

const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const madridDate = new Intl.DateTimeFormat("es-ES", {
  timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});
const states = { won: "Ganada", lost: "Fallo", void: "Anulada", pending: "Pendiente" };

export default function BotBankrollBoard({ report }: { report: BotBankroll | null }) {
  const router = useRouter();
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), 60_000);
    return () => clearInterval(timer);
  }, [router]);
  if (!report) return <p>No se pudo consultar la banca del bot.</p>;
  const { initial_cents, balance_cents, reserved_cents, days } = report;
  let settledCapital = initial_cents;
  const curve = [
    { cut: "Inicio", balance: initial_cents / 100 },
    ...[...days].reverse().map(day => {
      settledCapital += day.profit_cents;
      return { cut: madridDate.format(new Date(day.captured_at)), balance: settledCapital / 100 };
    }),
  ];
  const tickets = days.flatMap(day => day.tickets.map((ticket, index) => ({ day, ticket, index })));
  return (
    <section className="space-y-4 rounded-xl border border-surface-border bg-card p-4">
      <p className="text-sm text-slate-400">
        Los resultados en directo actualizan el saldo virtual. El bot reinvierte el retorno una vez confirmado el resultado final.
      </p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Banca inicial", initial_cents], ["Saldo virtual", balance_cents],
          ["En juego", reserved_cents], ["Beneficio neto", balance_cents + reserved_cents - initial_cents],
        ].map(([label, amount]) => (
          <div key={label}><dt className="text-sm text-slate-400">{label}</dt><dd className="font-semibold">{euros.format(Number(amount) / 100)}</dd></div>
        ))}
      </dl>
      <div>
        <h2 className="font-semibold">Evolución de la banca</h2>
        <p className="text-xs text-slate-400">Capital tras resolver las apuestas de cada corte.</p>
        <div className="mt-2 h-52">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={curve} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
              <CartesianGrid stroke="#334155" vertical={false} />
              <XAxis dataKey="cut" tick={{ fill: "#94a3b8", fontSize: 11 }} minTickGap={24} />
              <YAxis allowDecimals={false} tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={value => `${value} €`} />
              <Tooltip formatter={(value: number) => [euros.format(value), "Capital"]} contentStyle={{ background: "#0f172a", borderColor: "#334155" }} />
              <Line type="linear" dataKey="balance" stroke="#a78bfa" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      {days[0]?.fixtures.length > 0 && !days[0].tickets.length && (
        <p className="text-sm text-slate-400">
          Último corte · {madridDate.format(new Date(days[0].captured_at))} · El bot pasó en {days[0].fixtures.map(fixture => fixture.match).join(", ")}.
        </p>
      )}
      <div>
        <h2 className="mb-2 font-semibold">Historial de apuestas</h2>
        {!tickets.length ? <p className="text-sm text-slate-400">Todavía no hay apuestas registradas.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400"><tr>
                <th scope="col" className="px-2 py-2">Partido</th>
                <th scope="col" className="px-2 py-2">Apuesta y cuota</th>
                <th scope="col" className="px-2 py-2">Resultado</th>
              </tr></thead>
              <tbody>
                {tickets.map(({ day, ticket, index }) => {
                  const inPlay = ticket.legs.some(leg => leg.live);
                  return (
                  <tr key={`${day.captured_at}-${index}`} className="border-t border-surface-border align-top">
                    <td className="px-2 py-2">
                      {ticket.legs.map((leg, i) => (
                        <div key={i}>
                          <Link href={`/matches/${leg.match_id}`} className="hover:underline">{leg.match}</Link>
                          {leg.live && (
                            <div className="text-xs text-slate-400">
                              {leg.live.home}–{leg.live.away} · {leg.live.status === "HT" ? "Descanso"
                                : leg.live.status === "INT" || leg.live.status === "SUSP" ? "Interrumpido"
                                : leg.live.minute == null ? "En directo"
                                : `Min. ${leg.live.minute}${leg.live.extra ? `+${leg.live.extra}` : ""}`}
                            </div>
                          )}
                        </div>
                      ))}
                      <span className="text-xs text-slate-400">{madridDate.format(new Date(day.captured_at))}{day.policy_version === "manual-in-play-historical" && " · Manual en juego · cuota histórica"}</span>
                    </td>
                    <td className="px-2 py-2">
                      {ticket.legs.map((leg, i) => (
                        <div key={i}>
                          {MARKET_DISPLAY_NAMES[leg.market] ?? leg.market} · <strong>{leg.price.toFixed(2)}</strong>
                          {ticket.legs.length > 1 && leg.live?.goal_met && (
                            <div className="text-xs text-emerald-400">Gol cumplido</div>
                          )}
                        </div>
                      ))}
                      <span className="text-xs text-slate-400">{euros.format(ticket.stake_cents / 100)} apostados</span>
                    </td>
                    <td className="px-2 py-2 whitespace-nowrap">
                      <div className={ticket.state === "won" ? "text-emerald-400" : ticket.state === "lost" ? "text-red-400" : "text-slate-300"}>
                        {ticket.state === "pending" && inPlay ? "En juego" : states[ticket.state]}
                      </div>
                      {ticket.state !== "pending" && <span className="text-xs text-slate-400">{euros.format((ticket.returned_cents - ticket.stake_cents) / 100)} netos</span>}
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
