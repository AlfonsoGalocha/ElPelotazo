import Link from "next/link";
import type { BotBankroll } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";

const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const madridTime = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" });
const states = { won: "Ganada", lost: "Perdida", void: "Anulada", pending: "Pendiente" };

const decisions = {
  goles: "Más de 1,5 o 2,5 goles", ambos_marcan: "Ambos marcan",
  doble_oportunidad: "Doble oportunidad del favorito", pasar: "Pasar",
};

export default function BotBankrollBoard({ report }: { report: BotBankroll | null }) {
  if (!report) return <p>No se pudo consultar la banca del bot.</p>;
  const { initial_cents, balance_cents, reserved_cents, days } = report;
  return (
    <section className="space-y-4 rounded-xl border border-surface-border bg-card p-5">
      <h2 className="text-lg font-semibold">Banca del bot</h2>
      <p className="text-sm text-slate-400">
        El bot decide antes del primer partido del día en La Liga, Premier y Serie A. Los cortes nuevos reparten todo el saldo virtual disponible si hay selecciones aptas.
      </p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Banca inicial", initial_cents], ["Saldo disponible", balance_cents],
          ["En juego", reserved_cents], ["Beneficio liquidado", balance_cents + reserved_cents - initial_cents],
        ].map(([label, amount]) => (
          <div key={label}><dt className="text-sm text-slate-400">{label}</dt><dd className="font-semibold">{euros.format(Number(amount) / 100)}</dd></div>
        ))}
      </dl>
      {!days.length && <p className="text-sm text-slate-400">El primer corte automático se registra durante la hora anterior al primer partido.</p>}
      {days.map((day) => (
        <article className="space-y-2 border-t border-surface-border pt-3" key={day.day}>
          <h3 className="font-medium">{day.day.split("-").reverse().join("/")} · decisión a las {madridTime.format(new Date(day.captured_at))}</h3>
          <p className="text-sm text-slate-400">Apostado {euros.format(day.tickets.reduce((sum, ticket) => sum + ticket.stake_cents, 0) / 100)} · Cuotas {day.odds_band.map((n) => n.toFixed(2)).join("–")} · Beneficio liquidado {euros.format(day.profit_cents / 100)}</p>
          {!day.tickets.length && <p className="text-sm text-slate-300">El bot decidió no apostar en este corte.</p>}
          <ul className="space-y-2 text-sm">
            {day.tickets.map((ticket) => (
              <li className="rounded border border-surface-border p-3" key={ticket.legs.map((leg) => leg.match_id).join("-")}>
                <div className="font-medium">
                  {ticket.legs.length === 1 ? "Simple" : "Doble"} · {euros.format(ticket.stake_cents / 100)} · {states[ticket.state]}
                  {ticket.state !== "pending" && ` · Retorno ${euros.format(ticket.returned_cents / 100)}`}
                </div>
                {ticket.legs.map((leg) => (
                  <div key={leg.match_id} className="mt-1 text-slate-300">
                    <Link href={`/matches/${leg.match_id}`} className="hover:underline">{leg.match}</Link>
                    {" · "}{MARKET_DISPLAY_NAMES[leg.market] ?? leg.market} · {(100 * leg.probability).toFixed(1)} % · {leg.price.toFixed(2)} · {leg.bookmaker}
                    {leg.probability_source === "original" && " · Probabilidad original"}
                  </div>
                ))}
              </li>
            ))}
          </ul>
          <div className="space-y-1 text-xs text-slate-400">
            {day.fixtures.map(({ match_id, match, assessed, eligible, decision, context }) => context ? (
              <details key={match_id}>
                <summary className="cursor-pointer">
                  {match} · {decision ? decisions[decision] : "Decisión"} · {eligible} mercados aptos de {assessed}
                </summary>
                {([["Local", context.home_form, context.home_standing], ["Visitante", context.away_form, context.away_standing]] as const).map(([label, form, standing]) => (
                  <p key={label} className="my-1">
                    {label} · {standing ? `Puesto ${standing.position}` : "Puesto sin registrar"} ·
                    Últimos {form.matches} partidos · {form.wins} ganados, {form.draws} empatados y {form.losses} perdidos
                  </p>
                ))}
                <p>Cara a cara · Ambos marcaron en {context.h2h.both_scored} de {context.h2h.matches} · Hubo más de 2,5 goles en {context.h2h.over_2_5}</p>
              </details>
            ) : (
              <p key={match_id}>{match} · {assessed} mercados evaluados · {eligible} aptos</p>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}
