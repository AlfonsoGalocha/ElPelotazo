"""Closing Line Value (CLV): cuanto se movio la cuota de consenso entre el
momento en que se generó una prediccion y el cierre del mercado (la ultima
cuota disponible antes del pitido inicial).

Por que importa: en la practica de apuestas cuantitativas, la linea de
cierre se considera la estimacion de probabilidad mas informada que
existe (ha absorbido toda la informacion disponible hasta el ultimo
momento). Un CLV positivo CONSISTENTE a lo largo de muchas predicciones es
evidencia de que el modelo detecta valor real antes de que el mercado lo
incorpore -- independientemente de si cada prediccion individual acierta
o falla. Es, deliberadamente, una TERCERA metrica distinta de
model_quality (Brier/log loss) y de market_strategy (ROI hipotetico): un
modelo puede tener buen CLV y mal ROI en una muestra pequenha por pura
varianza del resultado, o al reves -- ninguna de las tres sustituye a las
otras (seccion 25: "no confundas rendimiento predictivo con rentabilidad
hipotetica").

LIMITE HONESTO: solo puede calcularse para predicciones de partidos cuyas
cuotas se registraron DESPUES de anhadir `MatchOddsHistory` (tabla
append-only, ver db/models/matches.py) -- el historico de cuotas previo se
sobrescribia en cada refresco, asi que no hay manera de reconstruirlo
retroactivamente. `None` explicito en cualquier otro caso, nunca inventado.
"""

from __future__ import annotations


def compute_clv(odds_taken: float | None, closing_odds: float | None) -> float | None:
    """CLV en terminos de cuota: (odds_taken / closing_odds) - 1.

    Positivo = se tomo una cuota MEJOR que la de cierre (el mercado se
    movio A FAVOR despues de generar la prediccion -- señal de valor real
    detectado antes que el mercado). Negativo = el mercado se movio EN
    CONTRA. Cero = sin movimiento.
    """
    if odds_taken is None or closing_odds is None:
        return None
    if odds_taken <= 1.0 or closing_odds <= 1.0:
        return None
    return (odds_taken / closing_odds) - 1.0
