# Football Edge Detector

Plataforma de analisis estadistico y deteccion de edges en mercados de
futbol. **No** predice "quien gana": estima probabilidades para mercados de
goles (Over/Under, BTTS), las compara contra la probabilidad implicita en
las cuotas de mercado (sin vig) y reporta el **edge estadistico** resultante
— siempre junto a la confianza del modelo y la calidad de los datos usados,
nunca como una certeza.

```
DATA -> FEATURES -> MODELS -> CALIBRATION -> PROBABILITY -> MARKET -> EDGE -> BACKTEST -> EXPLANATION
```

Ver `docs/architecture.md` para el detalle de cada capa.

## Fase actual: MVP (Fase 1)

- **Competiciones**: La Liga, Premier League, Bundesliga, Serie A, Ligue 1.
- **Mercados**: Over 1.5 / Over 2.5 / Under 2.5 / Over 3.5 goles, BTTS.
- **Datos**: football-data.co.uk (resultados, tiros, corners, tarjetas,
  arbitro, cuotas de cierre). Sin xG en el MVP (adapter de Understat
  preparado pero deshabilitado, ver `docs/data_sources.md`).
- **Modelos**: baseline (media de liga), Poisson por features, Dixon-Coles
  (con correccion de correlacion en marcadores bajos), clasificador
  logistico, y un ensemble con peso aprendido por validacion.
- **Calibracion**: Brier Score, Log Loss, ECE, curvas de fiabilidad,
  Isotonic/Platt.
- **Backtesting**: walk-forward (expanding window), separando calidad del
  modelo de rendimiento hipotetico de estrategia de mercado.

## Arrancar en 3 comandos (SQLite, sin Docker)

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
python scripts/update_data.py && python scripts/train_models.py
uvicorn backend.app.main:app --reload
```

Y en otra terminal:

```bash
cd frontend && npm install && npm run dev
```

Abre `http://localhost:3000` para ver el dashboard, o `http://localhost:8000/docs`
para la API interactiva (Swagger). Ver `docs/development.md` para el flujo
completo (incluyendo Docker Compose con Postgres).

## IMPORTANTE: sobre los datos en este entorno

Este proyecto se construyo en un contenedor de desarrollo con el egress de
red restringido a un allowlist (PyPI, npm, GitHub) que **no incluye
football-data.co.uk**. Esto significa que, dentro de esa sandbox:

- El pipeline completo (ingesta -> normalizacion -> features -> entrenamiento
  -> prediccion -> API -> dashboard) se valido end-to-end con datos
  **sinteticos** generados por codigo (`backend/tests/fixtures/synthetic.py`),
  nunca presentados como partidos reales.
- El adapter de football-data.co.uk (`backend/app/ingestion/football_data/provider.py`)
  esta completo y su parser de CSV esta testeado unitariamente contra el
  formato real de la fuente, pero la descarga en si (`httpx.get`) no pudo
  ejecutarse contra la red real en este entorno.

**Para poblar la base de datos con partidos reales**, ejecuta
`python scripts/update_data.py` en un entorno con acceso a internet normal.
No se ha inventado ningun dato para simular que el sistema tiene resultados
reales (seccion 70 del brief original: "no inventes datos").

## Que funciona realmente (verificado en este entorno)

- ✅ Ingesta -> normalizacion -> persistencia en BD (con un provider de
  prueba que simula football-data.co.uk; el parser real de CSV tambien esta
  testeado por separado).
- ✅ Feature engineering completo, con tests de anti-leakage pasando.
- ✅ Los 4 modelos de goles entrenan y producen distribuciones validas
  (suman 1, Over1.5 >= Over2.5 >= Over3.5 en cada fila).
- ✅ Ensemble con peso aprendido por grid search en validacion.
- ✅ Calibracion (Brier/LogLoss/ECE) e Isotonic Regression mejorando ECE en
  un test dedicado.
- ✅ Backtesting walk-forward con folds cronologicamente disjuntos.
- ✅ API FastAPI completa (13 endpoints), probada con `TestClient` end-to-end
  (ingesta real -> train real -> prediccion real -> serializacion HTTP).
- ✅ Dashboard Next.js (Today / Match detail / Top Signals), verificado con
  capturas de pantalla renderizando datos reales de la API.
- ✅ 41 tests automatizados pasando (`pytest backend/tests`), incluyendo el
  test critico de leakage temporal.
- ⚠️ Descarga real de football-data.co.uk: implementada pero no ejecutable
  en este sandbox (ver arriba). Requiere validarse en un entorno con red.

## Estructura del repositorio

```
backend/app/        API, dominio, modelos, features, backtesting (ver docs/architecture.md)
backend/tests/      unit / integration / backtesting (+ fixtures sinteticas)
frontend/           Next.js + Tailwind (dashboard)
data/               raw / processed / external (vacio en git, se puebla con scripts/)
models/             artefactos joblib entrenados (vacio en git)
scripts/            wrappers finos sobre la CLI `football-edge`
docs/               arquitectura, fuentes de datos, modelado, backtesting, API
docker-compose.yml  backend + frontend + Postgres
```

## Documentacion

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/data_sources.md`](docs/data_sources.md)
- [`docs/feature_engineering.md`](docs/feature_engineering.md)
- [`docs/modeling.md`](docs/modeling.md)
- [`docs/backtesting.md`](docs/backtesting.md)
- [`docs/api.md`](docs/api.md)
- [`docs/development.md`](docs/development.md) (incluye el roadmap de fases futuras)
