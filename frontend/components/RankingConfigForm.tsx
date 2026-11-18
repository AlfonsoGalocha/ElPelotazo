"use client";

import { useState } from "react";
import { getRankingConfig, updateRankingConfig } from "@/lib/api";
import type { RankingConfig } from "@/lib/api";

// "Mejores oportunidades" (cambio de filosofia del ranking, seccion 7 del
// pedido real del usuario): filtro de cuotas REALES configurable en
// runtime, con 1.35-1.90 como default -- primera preferencia de esta app
// que de verdad se guarda (backend/app/services/ranking_config_service.py),
// nunca simulada. Recibe el valor inicial ya cargado por el Server
// Component (sin spinner al entrar a Configuracion); el PUT real pasa
// por aqui, "use client", porque necesita feedback inmediato de exito o
// del error de validacion real del backend (422).
export default function RankingConfigForm({ initial }: { initial: RankingConfig }) {
  const [config, setConfig] = useState(initial);
  const [minInput, setMinInput] = useState(String(initial.min_opportunity_odds));
  const [maxInput, setMaxInput] = useState(String(initial.max_opportunity_odds));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const isDefault =
    config.min_opportunity_odds === config.default_min_opportunity_odds &&
    config.max_opportunity_odds === config.default_max_opportunity_odds;

  async function handleSave(minOdds: number, maxOdds: number) {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateRankingConfig(minOdds, maxOdds);
      setConfig(updated);
      setMinInput(String(updated.min_opportunity_odds));
      setMaxInput(String(updated.max_opportunity_odds));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo guardar el filtro.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-surface-border bg-card p-4">
      <div className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Filtro de cuotas — &quot;Mejores oportunidades&quot;
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Solo cuotas REALES de mercado dentro de este rango (ambos límites inclusive) pueden
        aparecer en &quot;Oportunidades&quot; — nada por debajo del mínimo (demasiado poco
        rentable) ni por encima del máximo (demasiada incertidumbre), aunque su probabilidad o EV
        parezcan buenos. Por defecto {config.default_min_opportunity_odds.toFixed(2)}–
        {config.default_max_opportunity_odds.toFixed(2)}.
      </p>

      <form
        className="flex flex-wrap items-center gap-2 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          const min = Number(minInput);
          const max = Number(maxInput);
          if (Number.isNaN(min) || Number.isNaN(max)) {
            setError("Introduce dos números válidos.");
            return;
          }
          handleSave(min, max);
        }}
      >
        <label htmlFor="min_opportunity_odds" className="text-slate-400">
          Cuota mínima:
        </label>
        <input
          id="min_opportunity_odds"
          type="number"
          step="0.01"
          min="1.01"
          value={minInput}
          onChange={(e) => setMinInput(e.target.value)}
          className="w-20 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        />
        <span className="text-slate-500">y</span>
        <label htmlFor="max_opportunity_odds" className="text-slate-400">
          máxima:
        </label>
        <input
          id="max_opportunity_odds"
          type="number"
          step="0.01"
          min="1.01"
          value={maxInput}
          onChange={(e) => setMaxInput(e.target.value)}
          className="w-20 rounded border border-surface-border bg-surface-raised px-2 py-1 text-slate-200"
        />
        <button
          type="submit"
          disabled={saving}
          className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-1 font-medium text-slate-100 transition-colors hover:bg-accent/20 disabled:opacity-50"
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
        {!isDefault && (
          <button
            type="button"
            disabled={saving}
            onClick={() => handleSave(config.default_min_opportunity_odds, config.default_max_opportunity_odds)}
            className="text-xs text-slate-500 underline hover:text-slate-300"
          >
            Restaurar por defecto
          </button>
        )}
      </form>

      {error && <p className="mt-2 text-xs text-red-300">{error}</p>}
      {saved && !error && (
        <p className="mt-2 text-xs text-edge-positive">
          Guardado: {config.min_opportunity_odds.toFixed(2)}–{config.max_opportunity_odds.toFixed(2)}. Ya se aplica a
          &quot;Oportunidades&quot;.
        </p>
      )}
    </div>
  );
}
