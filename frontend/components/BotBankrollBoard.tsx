"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { BotBankroll, BotPerformance, BotSimulations, BotTicket, BotTicketKind } from "@/lib/api";
import { MARKET_DISPLAY_NAMES } from "@/types";

const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const percent = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });
const madridDate = new Intl.DateTimeFormat("es-ES", {
  timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});
const states = { won: "Ganada", lost: "Fallo", void: "Anulada", pending: "Pendiente" };
const stateColor = (state: string) => state === "won" ? "text-emerald-400" : state === "lost" ? "text-red-400" : "text-slate-300";
const pct = (value: number | null | undefined) => value == null ? "—" : percent.format(value);
const eur = (cents: number | null | undefined) => cents == null ? "—" : euros.format(cents / 100);
const market = (key: string) => MARKET_DISPLAY_NAMES[key] ?? key;
const label = (key: string) => key.replaceAll("_", " ");
const PROFILE_LABELS: Record<string, string> = {
  prudente: "Prudente", moderado: "Moderado", agresivo_experimental: "Agresivo experimental", plano_10: "Stake fijo 10 €",
};

function PerformanceRow({ name, perf }: { name: string; perf: BotPerformance }) {
  return (
    <tr className="border-t border-surface-border">
      <th scope="row" className="px-2 py-1.5 text-left font-normal">{name}</th>
      <td className="px-2 py-1.5 text-right">{perf.tickets}</td>
      <td className="px-2 py-1.5 text-right">{perf.won}/{perf.won + perf.lost}</td>
      <td className="px-2 py-1.5 text-right">{pct(perf.hit_rate)}</td>
      <td className="px-2 py-1.5 text-right">{eur(perf.staked_cents)}</td>
      <td className={`px-2 py-1.5 text-right ${perf.profit_cents > 0 ? "text-emerald-400" : perf.profit_cents < 0 ? "text-red-400" : ""}`}>{eur(perf.profit_cents)}</td>
      <td className="px-2 py-1.5 text-right">{pct(perf.roi)}</td>
    </tr>
  );
}

function PerformanceTable({ rows }: { rows: [string, BotPerformance][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-slate-400"><tr>
          <th scope="col" className="px-2 py-1.5 text-left">Grupo</th>
          <th scope="col" className="px-2 py-1.5 text-right">Apuestas</th>
          <th scope="col" className="px-2 py-1.5 text-right">Aciertos</th>
          <th scope="col" className="px-2 py-1.5 text-right">% acierto</th>
          <th scope="col" className="px-2 py-1.5 text-right">Apostado</th>
          <th scope="col" className="px-2 py-1.5 text-right">Neto</th>
          <th scope="col" className="px-2 py-1.5 text-right">ROI</th>
        </tr></thead>
        <tbody>{rows.map(([name, perf]) => <PerformanceRow key={name} name={name} perf={perf} />)}</tbody>
      </table>
    </div>
  );
}

function LegLine({ ticket, index }: { ticket: BotTicket; index: number }) {
  const leg = ticket.legs[index];
  const state = ticket.leg_states?.[index];
  return (
    <div>
      <Link href={`/matches/${leg.match_id}`} className="hover:underline">{leg.match}</Link>
      <div className="text-xs text-slate-400">
        {market(leg.market)} · <strong className="text-slate-200">{leg.price.toFixed(2)}</strong>
        {leg.probability != null && <> · p {pct(leg.probability)}</>}
        {leg.confidence != null && <> · conf. {leg.confidence.toFixed(2)}</>}
        {ticket.legs.length > 1 && state && <span className={`ml-1 ${stateColor(state)}`}>· {states[state]}</span>}
        {leg.live?.goal_met && <span className="ml-1 text-emerald-400">· Gol cumplido</span>}
      </div>
      {leg.live && (
        <div className="text-xs text-slate-400">
          {leg.live.home}–{leg.live.away} · {leg.live.status === "HT" ? "Descanso"
            : leg.live.status === "INT" || leg.live.status === "SUSP" ? "Interrumpido"
            : leg.live.minute == null ? "En directo"
            : `Min. ${leg.live.minute}${leg.live.extra ? `+${leg.live.extra}` : ""}`}
        </div>
      )}
    </div>
  );
}

export default function BotBankrollBoard({ report, simulations }: { report: BotBankroll | null; simulations?: BotSimulations | null }) {
  const router = useRouter();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), 60_000);
    return () => clearInterval(timer);
  }, [router]);
  if (!report) return <p>No se pudo consultar la banca del bot.</p>;
  const { initial_cents, balance_cents, reserved_cents, days, summary, kind_labels } = report;
  let settledCapital = initial_cents;
  const curve = [
    { cut: "Inicio", balance: initial_cents / 100 },
    ...[...days].reverse().map(day => {
      settledCapital += day.profit_cents;
      return { cut: madridDate.format(new Date(day.captured_at)), balance: settledCapital / 100 };
    }),
  ];
  const tickets = days.flatMap(day => day.tickets.map((ticket, index) => ({ day, ticket, index })));
  const explained = days.filter(day => day.capital).slice(0, 5);
  const kinds = Object.keys(kind_labels) as BotTicketKind[];
  const active = report.risk.profiles[report.risk.active];
  return (
    <section className="space-y-6 rounded-xl border border-surface-border bg-card p-4">
      <p className="text-sm text-slate-400">
        Banca exclusivamente virtual. Los resultados en directo actualizan el saldo mostrado, pero el bot solo reinvierte retornos ya confirmados
        y nunca compromete capital reservado ni la reserva mínima de liquidez.
      </p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Banca inicial", eur(initial_cents)], ["Saldo disponible", eur(balance_cents)],
          ["Capital comprometido", eur(reserved_cents)], ["Beneficio neto", eur(balance_cents + reserved_cents - initial_cents)],
          ["ROI", pct(summary.all.roi)], ["Drawdown máximo", pct(summary.max_drawdown)],
          ["% aciertos", pct(summary.all.hit_rate)], ["Perfil de riesgo", PROFILE_LABELS[report.risk.active] ?? report.risk.active],
        ].map(([name, value]) => (
          <div key={name}><dt className="text-sm text-slate-400">{name}</dt><dd className="font-semibold">{value}</dd></div>
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

      <div>
        <h2 className="mb-1 font-semibold">Rendimiento</h2>
        <p className="mb-2 text-xs text-slate-400">Las apuestas manuales se muestran aparte y no se atribuyen al bot.</p>
        <PerformanceTable rows={[
          ["Decisiones automáticas", summary.automatic], ["Apuestas manuales", summary.manual],
          ...kinds.map(kind => [kind_labels[kind], summary.by_kind[kind]] as [string, BotPerformance]),
        ]} />
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
                <th scope="col" className="px-2 py-2">Tipo y cuota</th>
                <th scope="col" className="px-2 py-2">Prob. · confianza · EV</th>
                <th scope="col" className="px-2 py-2">Stake y riesgo</th>
                <th scope="col" className="px-2 py-2">Resultado</th>
              </tr></thead>
              <tbody>
                {tickets.map(({ day, ticket, index }) => {
                  const key = `${day.captured_at}-${index}`;
                  const inPlay = ticket.legs.some(leg => leg.live);
                  const isDouble = ticket.legs.length > 1;
                  const price = ticket.price ?? ticket.legs.reduce((acc, leg) => acc * leg.price, 1);
                  const probability = ticket.probability ?? (isDouble ? undefined : ticket.legs[0].probability);
                  return (
                    <Fragment key={key}>
                      <tr className="border-t border-surface-border align-top">
                        <td className="px-2 py-2">
                          {isDouble ? (
                            <button type="button" className="text-left hover:underline" aria-expanded={!!open[key]}
                                    onClick={() => setOpen(state => ({ ...state, [key]: !state[key] }))}>
                              {open[key] ? "▾" : "▸"} Combinada: {ticket.legs.map(leg => leg.match).join(" + ")}
                            </button>
                          ) : <LegLine ticket={ticket} index={0} />}
                          <span className="block text-xs text-slate-400">{madridDate.format(new Date(day.captured_at))}</span>
                        </td>
                        <td className="px-2 py-2">
                          <div>{kind_labels[ticket.kind] ?? ticket.kind}{ticket.strategy === "alta_probabilidad" && " · alta probabilidad"}</div>
                          <div className="text-xs text-slate-400">
                            {isDouble ? "Cuota combinada sintética" : market(ticket.legs[0].market)} · <strong className="text-slate-200">{price.toFixed(2)}</strong>
                            {ticket.kind === "manual_live" && " · cuota histórica prepartido"}
                          </div>
                        </td>
                        <td className="px-2 py-2 text-xs text-slate-300">
                          <div>p {pct(probability)}{ticket.effective_probability != null && <> → efectiva {pct(ticket.effective_probability)}</>}</div>
                          <div>confianza {ticket.confidence != null ? ticket.confidence.toFixed(2) : "—"} · EV {ticket.ev != null ? pct(ticket.ev) : "—"}</div>
                        </td>
                        <td className="px-2 py-2 text-xs text-slate-300">
                          <div className="text-sm text-slate-100">{eur(ticket.stake_cents)}</div>
                          <div>{ticket.bankroll_share != null ? `${pct(ticket.bankroll_share)} de la banca` : "—"}</div>
                          <div>Riesgo máx. {eur(ticket.max_loss_cents ?? ticket.stake_cents)}</div>
                        </td>
                        <td className="whitespace-nowrap px-2 py-2">
                          <div className={stateColor(ticket.state)}>{ticket.state === "pending" && inPlay ? "En juego" : states[ticket.state]}</div>
                          {ticket.state !== "pending" && <span className="text-xs text-slate-400">{eur(ticket.returned_cents - ticket.stake_cents)} netos</span>}
                        </td>
                      </tr>
                      {(ticket.reason || (isDouble && open[key])) && (
                        <tr className="align-top">
                          <td colSpan={5} className="px-2 pb-2 text-xs text-slate-400">
                            {isDouble && open[key] && (
                              <div className="mb-1 space-y-1 border-l border-surface-border pl-3">
                                {ticket.legs.map((_, i) => <LegLine key={i} ticket={ticket} index={i} />)}
                                {ticket.double && (
                                  <div>Sensibilidad: {ticket.double.sensitivity.map(row => `${label(row.scenario)} ${pct(row.probability)} (EV ${pct(row.ev)})`).join(" · ")}</div>
                                )}
                              </div>
                            )}
                            {ticket.reason && <div>Motivo: {ticket.reason}</div>}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <h2 className="mb-1 font-semibold">Explicación de decisiones</h2>
        <p className="mb-2 text-xs text-slate-400">
          En cada corte el bot evalúa conjuntamente todas las alternativas (no apostar, individuales, combinadas y mezclas) y elige la de mayor
          crecimiento logarítmico en el peor escenario de dependencia entre partidos (la incertidumbre de calibración ya está descontada en la probabilidad efectiva). Las cuotas combinadas son sintéticas, no ofertas de una casa.
        </p>
        {!explained.length ? <p className="text-sm text-slate-400">Aún no hay cortes con el nuevo motor de capital.</p> : explained.map(day => {
          const capital = day.capital!;
          return (
            <details key={day.captured_at} className="mb-2 rounded-lg border border-surface-border p-3" open={day === explained[0]}>
              <summary className="cursor-pointer text-sm">
                {madridDate.format(new Date(day.captured_at))} · <strong>{capital.chosen}</strong> · {capital.evaluated_alternatives} alternativas
              </summary>
              <p className="mt-2 text-sm text-slate-300">{capital.reason}</p>
              {capital.multiplier < 1 && <p className="text-xs text-amber-400">Drawdown activo: fracción de Kelly multiplicada por {capital.multiplier.toFixed(2)}.</p>}
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-slate-400"><tr>
                    <th scope="col" className="px-2 py-1 text-left">Alternativa</th>
                    <th scope="col" className="px-2 py-1 text-right">Stakes</th>
                    <th scope="col" className="px-2 py-1 text-right">Beneficio esperado</th>
                    <th scope="col" className="px-2 py-1 text-right">Crec. robusto</th>
                    <th scope="col" className="px-2 py-1 text-right">P(pérdida)</th>
                    <th scope="col" className="px-2 py-1 text-right">Pérdida máx.</th>
                    <th scope="col" className="px-2 py-1 text-left">Veredicto</th>
                  </tr></thead>
                  <tbody>
                    {capital.alternatives.map(alt => (
                      <tr key={alt.description} className={`border-t border-surface-border ${alt.chosen ? "text-emerald-300" : ""}`}>
                        <td className="px-2 py-1">{alt.description}</td>
                        <td className="px-2 py-1 text-right">{alt.stakes_cents?.map(s => eur(s)).join(" + ") ?? "—"}</td>
                        <td className="px-2 py-1 text-right">{eur(alt.expected_profit_cents)}</td>
                        <td className="px-2 py-1 text-right">{alt.robust_log_growth != null ? pct(alt.robust_log_growth) : "—"}</td>
                        <td className="px-2 py-1 text-right">{pct(alt.loss_probability)}</td>
                        <td className="px-2 py-1 text-right">{eur(alt.max_loss_cents)}</td>
                        <td className="px-2 py-1">{label(alt.verdict)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {capital.accepted_doubles.length > 0 && (
                <p className="mt-2 text-xs text-slate-400">Combinadas candidatas: {capital.accepted_doubles.map(d =>
                  `${d.matches.join(" + ")} @ ${d.price.toFixed(2)} (p efectiva ${pct(d.effective_probability)}, EV con dependencia adversa ${pct(d.robust_ev)})`).join(" · ")}</p>
              )}
              <ul className="mt-2 space-y-1 text-xs text-slate-400">
                {day.fixtures.map(fixture => (
                  <li key={fixture.match_id}>
                    <span className="text-slate-300">{fixture.match}</span> · {fixture.eligible}/{fixture.assessed} mercados elegibles
                    {fixture.combo_rejected && fixture.combo_rejected.length > 0 && (
                      <> · componentes descartados: {fixture.combo_rejected.slice(0, 3).map(r =>
                        `${market(r.market)} ${r.price.toFixed(2)} (p ${pct(r.probability)}, EV ${r.ev != null ? pct(r.ev) : "—"}: ${r.reasons.map(label).join(", ")})`).join(" · ")}</>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          );
        })}
      </div>

      <div>
        <h2 className="mb-1 font-semibold">Gestión del riesgo</h2>
        <p className="mb-2 text-xs text-slate-400">
          Kelly fraccionado sobre una probabilidad efectiva que se acerca a la de equilibrio cuando la confianza estadística es baja.
          Los límites se aplican a la cartera completa, incluidas las apuestas pendientes. El perfil agresivo requiere autorización explícita
          {report.risk.authorized_experimental ? " (autorizado)." : " (no autorizado)."}
          {report.risk.note && ` Aviso: ${label(report.risk.note)}.`}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-slate-400"><tr>
              <th scope="col" className="px-2 py-1 text-left">Perfil</th>
              <th scope="col" className="px-2 py-1 text-right">Fracción Kelly</th>
              <th scope="col" className="px-2 py-1 text-right">Máx. por apuesta</th>
              <th scope="col" className="px-2 py-1 text-right">Máx. combinada</th>
              <th scope="col" className="px-2 py-1 text-right">Exposición total</th>
              <th scope="col" className="px-2 py-1 text-right">Reserva mínima</th>
              <th scope="col" className="px-2 py-1 text-right">Pérdida máx. escenario</th>
            </tr></thead>
            <tbody>
              {Object.values(report.risk.profiles).map(profile => (
                <tr key={profile.name} className={`border-t border-surface-border ${profile.name === active?.name ? "text-violet-300" : ""}`}>
                  <td className="px-2 py-1">{profile.label}{profile.experimental && " ⚠"}{profile.name === active?.name && " · activo"}</td>
                  <td className="px-2 py-1 text-right">{profile.kelly_fraction.toFixed(2)}</td>
                  <td className="px-2 py-1 text-right">{pct(profile.max_single_fraction)} · {eur(profile.max_single_at_equity_cents)}</td>
                  <td className="px-2 py-1 text-right">{pct(profile.max_double_fraction)}</td>
                  <td className="px-2 py-1 text-right">{pct(profile.max_exposure_fraction)}</td>
                  <td className="px-2 py-1 text-right">{pct(profile.min_reserve_fraction)}</td>
                  <td className="px-2 py-1 text-right">{pct(profile.max_scenario_loss_fraction)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {simulations && (
          <details className="mt-2">
            <summary className="cursor-pointer text-sm">Simulación de perfiles ({simulations.paths} trayectorias × {simulations.bets} apuestas)</summary>
            <p className="mt-1 text-xs text-slate-400">{simulations.assumptions}</p>
            {Object.entries(simulations.worlds).map(([world, rows]) => (
              <div key={world} className="mt-2 overflow-x-auto">
                <p className="text-xs font-semibold text-slate-300">Escenario: {label(world)}</p>
                <table className="w-full text-xs">
                  <thead className="text-slate-400"><tr>
                    <th scope="col" className="px-2 py-1 text-left">Perfil</th>
                    <th scope="col" className="px-2 py-1 text-right">Banca mediana</th>
                    <th scope="col" className="px-2 py-1 text-right">P5–P95</th>
                    <th scope="col" className="px-2 py-1 text-right">P(pérdida)</th>
                    <th scope="col" className="px-2 py-1 text-right">P(&lt;50 %)</th>
                    <th scope="col" className="px-2 py-1 text-right">Drawdown P95</th>
                    <th scope="col" className="px-2 py-1 text-right">Stake mediano / P99</th>
                    <th scope="col" className="px-2 py-1 text-right">Stakes ≥100 €</th>
                  </tr></thead>
                  <tbody>
                    {Object.entries(rows).map(([name, row]) => (
                      <tr key={name} className="border-t border-surface-border">
                        <td className="px-2 py-1">{PROFILE_LABELS[name] ?? name}</td>
                        <td className="px-2 py-1 text-right">{eur(row.median_final_cents)}</td>
                        <td className="px-2 py-1 text-right">{eur(row.p5_final_cents)} – {eur(row.p95_final_cents)}</td>
                        <td className="px-2 py-1 text-right">{pct(row.loss_probability)}</td>
                        <td className="px-2 py-1 text-right">{pct(row.halved_probability)}</td>
                        <td className="px-2 py-1 text-right">{pct(row.p95_max_drawdown)}</td>
                        <td className="px-2 py-1 text-right">{eur(row.median_stake_cents)} / {eur(row.p99_stake_cents)}</td>
                        <td className="px-2 py-1 text-right">{pct(row.share_stakes_100_plus)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </details>
        )}
      </div>

      <div>
        <h2 className="mb-1 font-semibold">Aprendizaje de gestión de capital</h2>
        <p className="mb-2 text-xs text-slate-400">{report.learning.note}</p>
        {report.shadow.cuts > 0 && (
          <p className="mb-2 text-xs text-slate-400">
            Bancas de referencia ({report.shadow.cuts} cortes, sin tocar la banca principal): {Object.entries(report.shadow.banks).map(([name, bank]) =>
              `${PROFILE_LABELS[name] ?? name} ${eur(bank.balance_cents)} (${bank.tickets} apuestas${bank.pending ? `, ${bank.pending} pendientes` : ""})`).join(" · ")}
          </p>
        )}
        {([
          ["by_kind", "Individuales frente a combinadas"], ["by_strategy", "Ventaja confirmada frente a alta probabilidad"],
          ["by_staking", "Stake anterior frente a dinámico"],
          ["by_odds", "Por rango de cuota"], ["by_probability", "Por rango de probabilidad"],
          ["by_confidence", "Por confianza estadística"], ["by_market", "Por mercado"], ["by_competition", "Por competición"],
        ] as const).map(([key, title]) => Object.keys(report.learning[key]).length > 0 && (
          <details key={key} className="mb-1">
            <summary className="cursor-pointer text-sm">{title}</summary>
            <PerformanceTable rows={Object.entries(report.learning[key]).map(([name, perf]) => [kind_labels[name as BotTicketKind] ?? label(name), perf])} />
          </details>
        ))}
      </div>
    </section>
  );
}
