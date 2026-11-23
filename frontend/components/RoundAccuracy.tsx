"use client";

import { useEffect, useState } from "react";
import { getRoundAccuracy, type RoundAccuracy } from "@/lib/api";

const euros = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });

function netLabel(cents: number) {
  return `${cents > 0 ? "+" : ""}${euros.format(cents / 100)}`;
}

function hitRate(wins: number, losses: number) {
  const evaluated = wins + losses;
  return evaluated ? new Intl.NumberFormat("es-ES", {
    style: "percent", maximumFractionDigits: 1,
  }).format(wins / evaluated) : "—";
}

export default function RoundAccuracyPanel() {
  const [rounds, setRounds] = useState<RoundAccuracy[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const rows = await getRoundAccuracy();
        if (active) {
          setRounds(rows);
          setStatus("ready");
        }
      } catch {
        if (active) setStatus("error");
      } finally {
        if (active) timer = setTimeout(refresh, 60_000);
      }
    }
    void refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);

  const total = rounds.reduce((sum, row) => ({
    wins: sum.wins + row.model.wins, losses: sum.losses + row.model.losses,
    pending: sum.pending + row.pending, withoutForecast: sum.withoutForecast + row.model.without_forecast,
  }), { wins: 0, losses: 0, pending: 0, withoutForecast: 0 });
  const awaitingSettlement = rounds.reduce((sum, row) => sum + row.model.awaiting_settlement, 0);
  const staked = rounds.reduce((sum, row) => sum + row.flat_stake.staked_cents, 0);
  const returned = rounds.reduce((sum, row) => sum + row.flat_stake.returned_cents, 0);
  const net = returned - staked;
  const cancelled = rounds.reduce((sum, row) => sum + row.cancelled, 0);

  return (
    <section className="space-y-3 rounded-xl border border-surface-border bg-card p-4">
      <h2 className="font-semibold text-slate-100">Acierto del modelo por jornada</h2>
      <p className="text-sm text-slate-400">
        Para cada partido de la última jornada iniciada se elige el pronóstico prepartido con mayor probabilidad original que alcance el 65 % y tenga cuota guardada entre 1,30 y 1,80.
        El porcentaje usa solo los aciertos y fallos confirmados. Se actualiza cada minuto.
      </p>
      {status === "loading" && <p className="text-sm text-slate-400">Cargando resultados…</p>}
      {status === "error" && <p className="text-sm text-amber-300">No se pudo actualizar el acierto. Se reintentará automáticamente.</p>}
      {status === "ready" && !rounds.length && <p className="text-sm text-slate-400">Todavía no hay jornadas iniciadas.</p>}
      {!!rounds.length && (
        <>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <strong className="text-2xl text-slate-100">{hitRate(total.wins, total.losses)}</strong>
            <span className="text-sm text-slate-300">{total.wins} aciertos · {total.losses} fallos</span>
            <span className="text-sm text-slate-400">{total.pending} pendientes · {total.withoutForecast} finalizados sin selección apta</span>
            {awaitingSettlement > 0 && <span className="text-sm text-slate-400">{awaitingSettlement} sin liquidación verificable</span>}
            {cancelled > 0 && <span className="text-sm text-slate-400">{cancelled} anulados</span>}
          </div>
          <div className="rounded-lg border border-surface-border p-3">
            <h3 className="text-sm font-semibold">Simulación de 10 € por pronóstico</h3>
            <p className="my-1 text-xs text-slate-400">Una apuesta simple por partido, a la cuota guardada. Solo se incluyen los pronósticos liquidados.</p>
            <dl className="mt-2 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <div><dt className="text-slate-400">Apostado</dt><dd>{euros.format(staked / 100)}</dd></div>
              <div><dt className="text-slate-400">Retorno</dt><dd>{euros.format(returned / 100)}</dd></div>
              <div><dt className="text-slate-400">Beneficio neto</dt><dd className={net > 0 ? "text-emerald-400" : net < 0 ? "text-red-400" : ""}>{netLabel(net)}</dd></div>
              <div><dt className="text-slate-400">Rentabilidad</dt><dd>{staked ? new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(net / staked) : "—"}</dd></div>
            </dl>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full whitespace-nowrap text-left text-sm">
              <thead className="text-slate-400">
                <tr>{["Liga", "Jornada", "Acierto", "Aciertos", "Fallos", "Pendientes", "Sin selección apta", ...(awaitingSettlement ? ["Sin liquidar"] : []), ...(cancelled ? ["Anulados"] : []), "Neto a 10 €"].map(label =>
                  <th key={label} scope="col" className="px-2 py-2">{label}</th>
                )}</tr>
              </thead>
              <tbody>
                {rounds.map(row => (
                  <tr key={row.competition} className="border-t border-surface-border text-slate-300">
                    {[row.name, row.matchday, hitRate(row.model.wins, row.model.losses), row.model.wins, row.model.losses, row.pending, row.model.without_forecast,
                      ...(awaitingSettlement ? [row.model.awaiting_settlement] : []), ...(cancelled ? [row.cancelled] : []), row.flat_stake.staked_cents ? netLabel(row.flat_stake.returned_cents - row.flat_stake.staked_cents) : "—"].map((value, index) =>
                      <td key={index} className={index === 2 ? "px-2 py-2 font-semibold text-slate-100" : "px-2 py-2"}>{value}</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
