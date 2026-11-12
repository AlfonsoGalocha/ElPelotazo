"""Eliminacion del margen (vig/overround) de una casa de apuestas.

Metodo usado: normalizacion proporcional (basic method).
    overround = sum(implied_probabilities) - 1
    no_vig_probability_i = implied_probability_i / sum(implied_probabilities)

Es el metodo mas simple y transparente. Existen alternativas mas
sofisticadas (metodo de Shin, que modela probabilidad de informacion
privilegiada) que quedan documentadas como mejora futura pero no se
implementan en el MVP para mantener el calculo auditable y simple.

Investigado de nuevo en la sesion de "motor profesional de prediccion"
(2026-11): se intento verificar la formula cerrada de Shin (la expresion
con raiz cuadrada y el parametro z de "dinero informado", resuelto para
que las probabilidades resultantes sumen 1) contra una fuente autoritativa
antes de implementarla, siguiendo el mismo principio que el resto de este
proyecto ("nunca inventar, todo auditable"). El entorno de esta sesion no
tenia acceso de red a ninguna fuente academica/de referencia para
confirmarla (arxiv.org, karlwhelan.com, en.wikipedia.org -- todas
bloqueadas por la politica de red del entorno). Implementar una formula de
probabilidad sin poder verificarla es exactamente el tipo de riesgo que
este modulo existe para evitar: una formula sutilmente incorrecta
corromperia `market_probability`/edge en silencio, peor que no
implementarla. Queda deliberadamente deferida otra vez, con esta
justificacion explicita en vez de un "TODO" sin contexto -- una sesion
futura con acceso a una fuente verificable (o a la que el usuario pegue el
texto exacto del paper de Shin) puede completarla con los tests de
regresion que ya exigiria cualquier cambio en este modulo.

Solo aplica cuando se tienen TODAS las selecciones de un mercado (p.ej.
Over 2.5 y Under 2.5 a la vez). Si solo hay una cuota, no se puede quitar el
vig y se debe usar la probabilidad implicita bruta, dejandolo explicito.
"""

from __future__ import annotations

from backend.app.market.implied_probability import implied_probability


def overround(odds_by_selection: dict[str, float]) -> float:
    return sum(implied_probability(o) for o in odds_by_selection.values()) - 1.0


def no_vig_probabilities(odds_by_selection: dict[str, float]) -> dict[str, float]:
    implied = {sel: implied_probability(o) for sel, o in odds_by_selection.items()}
    total = sum(implied.values())
    return {sel: p / total for sel, p in implied.items()}
