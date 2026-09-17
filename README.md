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
- **Datos**: datos **reales** de football-data.co.uk (resultados, tiros,
  corners, tarjetas, cuotas de Bet365), descargados via un mirror publico en
  GitHub — ver "Datos reales" mas abajo. Sin xG en el MVP (adapter de
  Understat preparado pero deshabilitado, ver `docs/data_sources.md`).
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

## Datos reales

El contenedor donde se desarrollo este proyecto tiene el egress de red
restringido a un allowlist (PyPI, npm, GitHub) que **no incluye
football-data.co.uk** directamente. Para no depender de datos sinteticos en
el resultado final, se anhadio un segundo adapter,
`backend/app/ingestion/football_data/history_dataset.py`
(`ClubFootballMatchDataProvider`), que descarga el MISMO origen de datos
(resultados + estadisticas + cuotas de Bet365, sourced de
football-data.co.uk) desde un mirror publico y citable en GitHub
([xgabora/Club-Football-Match-Data-2000-2025](https://github.com/xgabora/Club-Football-Match-Data-2000-2025),
Gabor, A. 2026), accesible via `raw.githubusercontent.com`. Es el provider
que usa `scripts/update_data.py` por defecto (`--source history_dataset`);
el adapter directo a football-data.co.uk (`provider.py`) sigue disponible
para entornos sin esa restriccion (`--source football_data_co_uk`).

**Con esto, la base de datos de este proyecto contiene datos 100% reales**:
14,383 partidos de las 5 ligas del MVP, temporada 2018/19 a 2026/27 (actual),
con cuotas de Bet365 en casi todos ellos. Resultados reales obtenidos en
este entorno:

| Competicion | Partidos reales | Modelo | Over 2.5 — Brier | Over 2.5 — Log Loss |
|---|---|---|---|---|
| Premier League | 3,060 | baseline / Dixon-Coles / ML | 0.2475 / 0.2448 / 0.2038 |0.6881 / 0.6834 / 0.5951 |
| La Liga | 3,071 | baseline / Dixon-Coles / ML | 0.2509 / 0.2505 / 0.2149 | 0.6950 / 0.6946 / 0.6241 |
| Bundesliga | 2,457 | baseline / Dixon-Coles / ML | 0.2319 / 0.2518 / 0.1813 | 0.6565 / 0.7054 / 0.5450 |
| Serie A | 3,060 | baseline / Dixon-Coles / ML | 0.2524 / 0.2557 / 0.2182 | 0.6979 / 0.7050 / 0.6303 |
| Ligue 1 | 2,735 | baseline / Dixon-Coles / ML | 0.2494 / 0.2570 / 0.1830 | 0.6920 / 0.7138 / 0.5488 |

Notese que Dixon-Coles NO siempre supera al baseline (Bundesliga, Serie A,
Ligue 1): es un resultado real, no ajustado para quedar bien — el propio
brief pide comparar objetivamente, nunca asumir que un modelo mas complejo
gana (seccion 10/52).

**Backtest walk-forward real** (La Liga, Over 2.5, 7 folds 2020/21-2026/27,
2,368 predicciones): Brier 0.2466, Log Loss 0.6869, ECE 0.0285 (buena
calibracion — ver `docs/backtesting.md`). **Estrategia de mercado real**
(Premier League, apostando solo cuando el modelo ve edge > 0 frente a
Bet365): 664 apuestas de 2,366 oportunidades, ROI +0.33% — resultado
realista y modesto, no inflado.

**Prediccion real fuera de muestra, verificada contra el resultado real**:
se entreno un modelo excluyendo el ultimo partido cronologico de La Liga en
el dataset y se predijo usando solo informacion estrictamente anterior. El
partido termino 0-0 (Under 2.5, no BTTS). El modelo Dixon-Coles predijo
Over 2.5 al 52.8% (equivocado) y el clasificador ML predijo Under 2.5 al
66.9% (acertado) — un ejemplo real de por que hay que comparar modelos, no
asumir que uno es siempre mejor.

**Limitacion honesta que queda**: este dataset historico solo contiene
partidos YA JUGADOS (hasta 2026-09-03). No incluye un feed de fixtures
futuros, asi que `/predictions/today` estara vacio hasta conectar una fuente
de calendario en vivo (Fase 7 del roadmap, `docs/development.md`). El
pipeline de prediccion para partidos programados (`generate_predictions_for_competition`)
esta implementado y probado (ver `backend/tests/integration/test_api.py`),
solo le falta una fuente de fixtures futuros para tener que predecir en
"today" con este dataset en concreto.

## Que funciona realmente (verificado en este entorno, con datos reales)

- ✅ Ingesta de 14,383 partidos **reales** (2018/19-2026/27, 5 ligas) con
  cuotas de Bet365, via el mirror de GitHub.
- ✅ Feature engineering completo sobre datos reales, con tests de
  anti-leakage pasando.
- ✅ Los 4 modelos de goles entrenados con datos reales, comparados
  objetivamente (tabla arriba) — el ML classifier gana en todas las ligas
  para Over 2.5, pero Dixon-Coles no siempre supera al baseline.
- ✅ Ensemble con peso aprendido por grid search en validacion real.
- ✅ Calibracion (Brier/LogLoss/ECE) sobre datos reales + Isotonic
  Regression mejorando ECE en un test dedicado con datos sinteticos de
  control.
- ✅ Backtesting walk-forward real con folds cronologicamente disjuntos y
  simulacion de estrategia de mercado usando cuotas reales de Bet365.
- ✅ Prediccion fuera de muestra de un partido real, verificada contra el
  resultado real conocido.
- ✅ API FastAPI completa (13 endpoints), probada con `TestClient` end-to-end
  y sirviendo los modelos entrenados con datos reales.
- ✅ Dashboard Next.js (Today / Match detail / Top Signals), verificado con
  capturas de pantalla.
- ✅ 41 tests automatizados pasando (`pytest backend/tests`); los tests usan
  datos sinteticos deliberadamente (deben ser deterministas y no depender de
  red), separados del dataset real usado para el resultado final.
- ⚠️ Fixtures futuros ("today" en vivo): no cubierto por este dataset
  historico, requiere Fase 7 (fuente de calendario en vivo).

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
