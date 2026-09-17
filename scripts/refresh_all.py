#!/usr/bin/env python3
"""Wrapper fino sobre `football-edge refresh`.

Un unico comando: descarga resultados historicos + fixtures reales,
reentrena los modelos y genera predicciones para todo lo programado.

Uso:
    python scripts/refresh_all.py                      # todo, desde cero
    python scripts/refresh_all.py --skip-historical     # solo fixtures+train+predict (mas rapido)
    python scripts/refresh_all.py --days 14             # ventana de prediccion mas amplia
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.cli import app  # noqa: E402

if __name__ == "__main__":
    app(["refresh", *sys.argv[1:]])
