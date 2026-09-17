# Desarrollo

## Arrancar en local (sin Docker)

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # SQLite por defecto, funciona sin tocar nada

python scripts/update_data.py          # ingesta real (mirror de GitHub, ver docs/data_sources.md)
python scripts/train_models.py         # entrena los 5 competitions con datos reales
uvicorn backend.app.main:app --reload  # API en :8000

cd frontend && npm install && npm run dev  # dashboard en :3000
```

## Arrancar con Docker

```bash
docker compose up --build
```

Backend en `:8000` (Postgres en `:5432`), frontend en `:3000`.

`docker-compose.yml` esta validado sintacticamente (`docker compose config`)
pero **no pudo construirse de extremo a extremo en el entorno donde se
desarrollo este MVP** (sandbox sin daemon Docker disponible). Antes de
depender de el en produccion, ejecuta `docker compose up --build` una vez en
una maquina con Docker real y revisa los logs de arranque de cada servicio.

## Tests

```bash
pytest backend/tests -v
```

Todos los tests usan datos **sinteticos** (`backend/tests/fixtures/synthetic.py`),
generados con una semilla fija — nunca dependen de red ni de datos reales.

## Calidad de codigo

```bash
ruff check backend scripts        # lint
black backend scripts             # formato
mypy backend/app --ignore-missing-imports --explicit-package-bases
```

## Roadmap (fases futuras, no implementadas en este MVP)

1. Mercados de tarjetas (arbitro como feature — ya preparado en el modelo de
   datos y `features/referee.py`).
2. Mercados de corners.
3. Mercados de tiros (team-level; luego jugador).
4. Jugadores (`PlayerMatchStatistics`, ya en el esquema, vacio).
5. Alineaciones/lesiones como "current context" separado del "base model"
   (seccion 33).
6. Datos live / modelo de estado de partido.
7. Simulacion Monte Carlo sobre la distribucion de goles.
8. Discovery engine (busqueda automatica de variables relevantes con
   validacion out-of-sample obligatoria).
9. Alertas configurables (edge/calibration/data_quality thresholds).
10. Otros deportes (NBA, NFL, tenis, MLB) reutilizando el dominio
    `Sport -> Competition -> Event -> Team -> Player -> Market -> Model -> Prediction`.
