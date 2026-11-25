"use client";

import Link from "next/link";
import { getBotPlan, type BotPlan, type BotPlanStep, type BotTicket } from "@/lib/api";
import { useCachedData } from "@/lib/clientCache";
import { MARKET_DISPLAY_NAMES } from "@/types";

const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const percent = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });
const time = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" });
const eur = (cents: number | null | undefined) => cents == null ? "—" : euros.format(cents / 100);
const pct = (value: number | null | undefined) => value == null ? "—" : percent.format(value);
const market = (key: string) => MARKET_DISPLAY_NAMES[key] ?? key;
const label = (key: string) => key.replaceAll("_", " ");
const states = { won: "Ganada", lost: "Fallo", void: "Anulada", pending: "Pendiente" };

function minutesUntil(iso: string) {
  const minutes = Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
  if (minutes <= 0) return "ahora";
  return minutes < 60 ? `en ${minutes} min` : `en ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function TicketLine({ ticket }: { ticket: BotTicket }) {
  const double = ticket.legs.length > 1;
  return (
    <li className="rounded border border-surface-border px-3 py-2 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span>{double ? (ticket.strategy === "alta_probabilidad" ? "Combinada de alta probabilidad" : "Combinada") : "Individual"} · <strong>{eur(ticket.stake_cents)}</strong>
          {ticket.bankroll_share != null && <span className="text-xs text-slate-400"> ({pct(ticket.bankroll_share)} de la banca)</span>}
        </span>
        <span className="text-xs text-slate-400">cuota {(ticket.price ?? ticket.legs[0].price).toFixed(2)}{double && " sintética"}
          {ticket.probability != null && <> · p {pct(ticket.probability)}</>}
          {ticket.confidence != null && <> · conf. {ticket.confidence.toFixed(2)}</>}
          {ticket.state && <> · {states[ticket.state]}</>}
        </span>
      </div>
      {ticket.legs.map(leg => (
        <div key={leg.match_id} className="text-xs text-slate-300">
          <Link href={`/matches/${leg.match_id}`} className="hover:underline">{leg.match}</Link> · {market(leg.market)} {leg.price.toFixed(2)}
        </div>
      ))}
      {ticket.reason && <div className="mt-1 text-xs text-slate-400">{ticket.reason}</div>}
    </li>
  );
}

function StepDetails({ step }: { step: BotPlanStep }) {
  return (
    <details className="mt-2 text-xs text-slate-400">
      <summary className="cursor-pointer">Por qué ({step.evaluated_alternatives ?? step.alternatives.length} alternativas evaluadas)</summary>
      <ul className="mt-2 space-y-1">
        {step.fixtures.map(fixture => (
          <li key={fixture.match_id}>
            <span className="text-slate-300">{fixture.match}</span> · {time.format(new Date(fixture.kickoff))}
            {fixture.assessed != null && <> · {fixture.eligible}/{fixture.assessed} mercados elegibles</>}
            {fixture.single && <> · candidata {market(fixture.single.market)} {fixture.single.price.toFixed(2)} (p {pct(fixture.single.probability)}
              → efectiva {pct(fixture.single.effective_probability)}, equilibrio {pct(1 / fixture.single.price)}, conf. {fixture.single.confidence.toFixed(2)})</>}
            {!fixture.single && fixture.combo_rejected && fixture.combo_rejected.length > 0 && <> · mejor descartada: {market(fixture.combo_rejected[0].market)} {fixture.combo_rejected[0].price.toFixed(2)}
              (EV {fixture.combo_rejected[0].ev != null ? pct(fixture.combo_rejected[0].ev) : "—"}: {fixture.combo_rejected[0].reasons.map(label).join(", ")})</>}
          </li>
        ))}
      </ul>
      {step.alternatives.length > 1 && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full">
            <thead><tr>
              <th scope="col" className="px-2 py-1 text-left">Alternativa</th>
              <th scope="col" className="px-2 py-1 text-right">Stakes</th>
              <th scope="col" className="px-2 py-1 text-right">Crec. robusto</th>
              <th scope="col" className="px-2 py-1 text-right">P(pérdida)</th>
              <th scope="col" className="px-2 py-1 text-left">Veredicto</th>
            </tr></thead>
            <tbody>
              {step.alternatives.map(alt => (
                <tr key={alt.description} className={`border-t border-surface-border ${alt.chosen ? "text-emerald-300" : ""}`}>
                  <td className="px-2 py-1">{alt.description}</td>
                  <td className="px-2 py-1 text-right">{alt.stakes_cents?.map(s => eur(s)).join(" + ") ?? "—"}</td>
                  <td className="px-2 py-1 text-right">{alt.robust_log_growth != null ? pct(alt.robust_log_growth) : "—"}</td>
                  <td className="px-2 py-1 text-right">{pct(alt.loss_probability)}</td>
                  <td className="px-2 py-1">{label(alt.verdict)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  );
}

export default function BotPlanBoard() {
  const { data: plan, error, refreshing } = useCachedData<BotPlan>("bot-plan", getBotPlan, { refreshMs: 120_000 });
  if (error) return <p className="text-sm text-slate-400">No se pudo calcular el plan del bot.</p>;
  if (!plan) return <p className="text-sm text-slate-400">Calculando el plan del día… (la primera vez tarda unos segundos)</p>;
  const next = plan.next;
  return (
    <section className="space-y-6 rounded-xl border border-surface-border bg-card p-4">
      <div className="rounded-lg border border-violet-400/40 bg-violet-400/5 p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-violet-300">Lo próximo</h2>
        {!next ? (
          <p className="mt-2 text-sm text-slate-300">No quedan partidos por decidir hoy.</p>
        ) : (
          <>
            <p className="mt-2 text-lg font-semibold">
              {time.format(new Date(next.cut_at))} · {next.tickets.length ? next.chosen : "No apostará"}
              <span className="ml-2 text-sm font-normal text-slate-400">({minutesUntil(next.cut_at)})</span>
            </p>
            <p className="text-sm text-slate-400">{next.fixtures.map(f => `${f.match} (${time.format(new Date(f.kickoff))})`).join(" · ")}</p>
            {next.tickets.length > 0 && <ul className="mt-2 space-y-2">{next.tickets.map((t, i) => <TicketLine key={i} ticket={t} />)}</ul>}
            <p className="mt-2 text-sm text-slate-300">{next.reason}</p>
            <StepDetails step={next} />
          </>
        )}
      </div>

      <div>
        <h2 className="mb-1 font-semibold">Camino del día</h2>
        <p className="mb-3 text-xs text-slate-400">{plan.note}</p>
        <ol className="space-y-3 border-l border-surface-border pl-4">
          {plan.executed.map(cut => (
            <li key={cut.captured_at} className="relative">
              <span className="absolute -left-[1.4rem] top-1 h-2.5 w-2.5 rounded-full bg-emerald-400" aria-hidden />
              <p className="text-sm"><strong>{time.format(new Date(cut.captured_at))}</strong> · Ejecutado ·{" "}
                {cut.tickets.length ? (cut.chosen ?? `${cut.tickets.length} apuestas`) : "Sin apuestas"}</p>
              {cut.fixtures.length > 0 && <p className="text-xs text-slate-400">{cut.fixtures.map(f => f.match).join(" · ")}</p>}
              {cut.tickets.length > 0 && <ul className="mt-1 space-y-1">{cut.tickets.map((t, i) => <TicketLine key={i} ticket={t} />)}</ul>}
            </li>
          ))}
          {plan.steps.map((step, index) => (
            <li key={step.cut_at} className="relative">
              <span className={`absolute -left-[1.4rem] top-1 h-2.5 w-2.5 rounded-full ${index === 0 ? "bg-violet-400" : "border border-slate-500 bg-card"}`} aria-hidden />
              <p className="text-sm"><strong>{time.format(new Date(step.cut_at))}</strong> · Previsto ·{" "}
                {step.tickets.length ? step.chosen : "No apostar"}
                <span className="text-xs text-slate-400"> · disponible {eur(step.capital_before.available)}
                  {step.capital_after.available !== step.capital_before.available && <> → {eur(step.capital_after.available)}</>}</span>
              </p>
              <p className="text-xs text-slate-400">{step.fixtures.map(f => `${f.match} (${time.format(new Date(f.kickoff))})`).join(" · ")}</p>
              {step.tickets.length > 0 && <ul className="mt-1 space-y-1">{step.tickets.map((t, i) => <TicketLine key={i} ticket={t} />)}</ul>}
              {index > 0 && <StepDetails step={step} />}
            </li>
          ))}
        </ol>
      </div>
      <p className="text-xs text-slate-500">
        Calculado a las {time.format(new Date(plan.generated_at))} con las cuotas y predicciones de ese momento
        {refreshing ? " · actualizando…" : ""}. Se recalcula cada pocos minutos; la decisión real solo se registra en cada corte.
      </p>
    </section>
  );
}
