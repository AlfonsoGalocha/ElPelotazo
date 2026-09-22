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
- **Mercados**: Over 1.5 / Over 2.5 / Under 2.5 / Over 3.5 goles, BTTS,
  Over/Under tarjetas (3.5/4.5/5.5), Over/Under corners (8.5/9.5/10.5).
- **Datos**: resultados **reales** de football-data.co.uk (via mirror de
  GitHub) + **fixtures reales** de la temporada en curso (calendario
  oficial, via openfootball/football.json) — ver "Datos reales" mas abajo.
  Sin xG en el MVP (adapter de Understat preparado pero deshabilitado, ver
  `docs/data_sources.md`).
- **Modelos**: baseline (media de liga), Poisson por features, Dixon-Coles
  (con correccion de correlacion en marcadores bajos), clasificador
  logistico, ensemble con peso aprendido por validacion, y Poisson de total
  de partido para tarjetas/corners.
- **Calibracion**: Brier Score, Log Loss, ECE, curvas de fiabilidad,
  Isotonic/Platt.
- **Backtesting**: walk-forward (expanding window), separando calidad del
  modelo de rendimiento hipotetico de estrategia de mercado.
- **Dashboard**: partidos reales proximos (7 dias) agrupados por fecha,
  buscador de equipos/partidos, Top 5 predicciones (mezclando goles,
  tarjetas y corners), detalle de partido con pestanhas por familia de
  mercado y explicacion de factores.

## Arrancar (SQLite, sin Docker)

```bash
cp .env.example .env                    # IMPORTANTE: edita .env, no .env.example (ver nota abajo)
python3.10 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
football-edge refresh                   # datos + fixtures + cuotas + entrenamiento + predicciones, todo en uno
uvicorn backend.app.main:app --reload
```

Y en otra terminal:

```bash
cd frontend && npm install && npm run dev
```

Abre `http://localhost:3000` para ver el dashboard, o `http://localhost:8000/docs`
para la API interactiva (Swagger). Ver `docs/development.md` para el flujo
completo (incluyendo Docker Compose con Postgres).

**Sobre `.env`**: la app solo lee un fichero llamado exactamente `.env` en la
raiz del repo (`backend/app/config/settings.py`). Editar `.env.example`
directamente NO tiene ningun efecto — ni siquiera da un error, simplemente
usa los valores por defecto en silencio (lo que hace parecer "roto" algo
que en realidad nunca se cargo). Copia siempre el fichero primero:
`cp .env.example .env`, y edita ese `.env`.

**Si el dashboard muestra partidos que no tienen sentido** (equipos que no
juegan esta temporada en esa liga, partidos repetidos...): son casi
seguro restos de pruebas de una fase anterior del desarrollo que quedaron
en tu base de datos local (el `.db` de SQLite persiste entre ejecuciones
aunque el codigo cambie). Limpialos una vez con:

```bash
football-edge reset-scheduled        # borra solo partidos "scheduled" (no toca el historico real)
football-edge refresh --skip-historical
```

## Refresco automatico cada hora

Un partido no aparece en "Histórico" en tiempo real: aparece en cuanto el
pipeline vuelve a comprobar su estado (`Match.status == "finished"`, ver
`docs/data_sources.md`). Si no automatizas nada, eso solo pasa cuando
ejecutas `football-edge refresh` a mano. Dos formas de dejarlo en
automatico (elige una segun como despliegues):

**OJO con las cuotas de las APIs externas**: The Odds API (plan gratuito)
da solo 500 peticiones/mes. Pedir cuotas nuevas cada hora para las 5 ligas
agota esa cuota en menos de una semana sin ninguna ganancia real (las
cuotas de mercado no cambian tan rapido). Por eso el refresco horario NO
llama a `football-edge refresh` completo: separa lo que es horario
(fixtures nuevos + liquidar partidos terminados + predicciones, que no
gastan cuota externa) de lo que solo hace falta cada 6 horas (cuotas de
mercado).

**Docker Compose** (recomendado si ya usas `docker-compose.yml`): ya
incluye un servicio `scheduler` que hace exactamente ese reparto en bucle
(fixtures/liquidacion/predicciones cada hora, cuotas cada 6h) contra la
misma base de datos que `backend` — no hace falta nada mas, arranca solo
con `docker compose up`.

**venv local / servidor sin Docker**: usa cron. Con `crontab -e`, anhade
(ajusta la ruta al repo y al `.venv`):

```cron
0 * * * * cd /ruta/al/repo && .venv/bin/football-edge update-fixtures && .venv/bin/football-edge evaluate && .venv/bin/football-edge predict-upcoming >> logs/refresh.log 2>&1
0 */6 * * * cd /ruta/al/repo && .venv/bin/football-edge update-odds && .venv/bin/football-edge update-secondary-odds >> logs/odds.log 2>&1
```

La primera linea es horaria (fixtures + liquidacion de partidos terminados
+ predicciones, sin gastar cuota de API externa); la segunda son las
cuotas de mercado, cada 6 horas. Si prefieres simplicidad sobre cuidar la
cuota gratuita (por ejemplo, tienes un plan de pago de The Odds API), usa
`football-edge refresh --skip-historical` cada hora en su lugar --
`--skip-historical` evita que cada pasada vuelva a descargar temporadas
enteras de resultados pasados (lento e innecesario).

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
~14,300 partidos JUGADOS de las 5 ligas del MVP (temporada 2018/19 a
2026/27) con cuotas de Bet365, **mas ~1,550 partidos FUTUROS reales**
(calendario oficial de la temporada 2026/27, ver "Fixtures reales" abajo).

### ⚠️ Dos bugs de leakage encontrados y corregidos durante el desarrollo

Mientras se verificaban las explicaciones de las predicciones contra datos
reales, se detectaron dos bugs de fuga de informacion (usar el resultado del
propio partido a predecir como si fuera una feature de entrada). Se
documentan aqui explicitamente porque afectaron a los numeros iniciales de
esta misma seccion en un momento anterior del desarrollo:

1. Las columnas crudas del propio partido (`home_shots_on_target`, `home_corners`, etc.,
   necesarias solo para calcular la forma de partidos FUTUROS de ese equipo)
   se colaban directamente en `feature_columns()`.
2. Una segunda variante del mismo bug: las versiones SIN ventana temporal
   generadas por `features/base.py` (`shots_for`, `corners_against`, etc.,
   antes de aplicarles `_avg_last5`) tambien se colaban.

Ambos corregidos en `features/goals.py::feature_columns()`, con dos tests de
regresion dedicados en `backend/tests/unit/test_features_leakage.py` que
comprueban explicitamente que esas columnas nunca vuelvan a aparecer. Tras
la segunda correccion, el clasificador ML dejo de "ganar en todas las
ligas" (ver tabla actualizada abajo): ese resultado anterior era en gran
parte el bug, no una ventaja real del modelo.

### Resultados reales (post-correccion, datos limpios)

| Competicion | Partidos jugados | Modelo | Over 2.5 — Brier | Over 2.5 — Log Loss |
|---|---|---|---|---|
| Premier League | 3,060 | baseline / Dixon-Coles / ML | 0.2475 / 0.2449 / 0.2529 | 0.6881 / 0.6835 / 0.7030 |
| La Liga | 3,071 | baseline / Dixon-Coles / ML | 0.2509 / 0.2505 / 0.2777 | 0.6950 / 0.6946 / 0.7547 |
| Bundesliga | 2,457 | baseline / Dixon-Coles / ML | 0.2319 / 0.2518 / 0.2299 | 0.6565 / 0.7053 / 0.6500 |
| Serie A | 3,060 | baseline / Dixon-Coles / ML | 0.2524 / 0.2557 / 0.2763 | 0.6979 / 0.7050 / 0.7496 |
| Ligue 1 | 2,735 | baseline / Dixon-Coles / ML | 0.2494 / 0.2570 / 0.2669 | 0.6920 / 0.7138 / 0.7337 |

Con las features limpias, el clasificador ML (195 features, ~3,000 filas de
entrenamiento) sobreajusta y **pierde contra el baseline en 4 de 5 ligas**.
Esto es exactamente el resultado honesto que el brief pide poder ver
(seccion 10/52: nunca asumir que un modelo mas complejo gana; comparar
siempre contra baselines). El ensemble aprendido compensa esto dandole poco
o ningun peso al ML classifier cuando no aporta (ver `weight_statistical`
en cada `ModelVersion.metrics`).

**Backtest walk-forward real** (Dixon-Coles, Over 2.5, ~7 folds
2020/21-2026/27 por liga — Dixon-Coles no usa `feature_columns()` asi que
estos numeros nunca estuvieron afectados por los bugs anteriores):

| Competicion | Predicciones | Brier | Log Loss | ECE | ROI hipotetico (Bet365) |
|---|---|---|---|---|---|
| La Liga | 2,690 | 0.2476 | 0.6889 | 0.0725 | -6.1% (769 apuestas) |
| Premier League | 2,706 | 0.2542 | 0.7022 | 0.0715 | +0.4% (665 apuestas) |
| Bundesliga | 2,124 | 0.2546 | 0.7051 | 0.0763 | -5.4% (633 apuestas) |
| Serie A | 2,738 | 0.2490 | 0.6916 | 0.0686 | -9.0% (967 apuestas) |
| Ligue 1 | 2,346 | 0.2547 | 0.7033 | 0.0628 | -3.2% (507 apuestas) |

ROI mayoritariamente negativo apostando siempre que el modelo ve edge > 0
frente a Bet365: es el resultado real, no se ha filtrado para que quede
bien. Esto es precisamente la diferencia entre **model quality** (Brier/ECE
razonables) y **market strategy performance** (seccion 27): un modelo
razonablemente calibrado no implica automaticamente una estrategia de
apuestas rentable frente a un bookmaker.

### Fixtures reales (calendario de la temporada 2026/27)

Ademas de resultados historicos, se anhadio un SEGUNDO adapter real,
`backend/app/ingestion/football_data/fixtures_provider.py`
(`OpenFootballFixturesProvider`), que descarga el calendario oficial de la
temporada en curso — partidos AUN NO JUGADOS, con equipos y fecha reales —
desde [openfootball/football.json](https://github.com/openfootball/football.json)
(dominio publico, actualizado a diario). Esto es lo que permite que
`/predictions/today` (ventana de 7 dias) muestre partidos que **de verdad
se van a jugar**, con historial real de ambos equipos.

Los ~97 nombres de equipo de esta fuente (formato oficial completo, p.ej.
"Real Madrid CF") se resuelven al mismo `team_id` que el dataset historico
("Real Madrid") via un diccionario de alias en `normalization/teams.py`,
verificado explicitamente: 0 equipos nuevos sin historial tras la ingesta.

**Limitacion honesta**: es un dataset comunitario, puede llevar retraso en
aplazamientos/cambios de horario de ultima hora. Se filtra explicitamente
cualquier fixture con fecha pasada para evitar mostrar como "programado" un
partido que ya se jugo en la realidad pero que la fuente aun no ha
actualizado con marcador.

### Prediccion real fuera de muestra, verificada contra el resultado real

Se entreno un modelo excluyendo el ultimo partido cronologico de La Liga en
el dataset y se predijo usando solo informacion estrictamente anterior. El
partido termino 0-0 (Under 2.5, no BTTS). El modelo Dixon-Coles predijo
Over 2.5 al 52.8% (equivocado); tras corregir el leakage, esto se mantiene
como un ejemplo honesto de que ningun modelo acierta siempre.

## Que funciona realmente (verificado en este entorno, con datos reales)

- ✅ Ingesta de ~14,300 partidos **reales jugados** (2018/19-2026/27, 5 ligas)
  con cuotas de Bet365, mas ~1,550 **fixtures reales futuros** de la
  temporada 2026/27, ambos via mirrors publicos de GitHub.
- ✅ Feature engineering completo sobre datos reales, con **3 tests de
  anti-leakage** que detectaron y bloquean 2 bugs reales encontrados durante
  el desarrollo (documentados arriba).
- ✅ Los 4 modelos de goles + 2 modelos de tarjetas/corners entrenados con
  datos reales, comparados objetivamente (tabla arriba) — resultado honesto:
  el ML classifier NO gana siempre, pierde en 4/5 ligas para Over 2.5.
- ✅ Ensemble con peso aprendido por grid search en validacion real.
- ✅ Calibracion (Brier/LogLoss/ECE) sobre datos reales + Isotonic
  Regression mejorando ECE en un test dedicado con datos sinteticos de
  control.
- ✅ Backtesting walk-forward real con folds cronologicamente disjuntos,
  separando model quality de market strategy performance con cuotas reales.
- ✅ Prediccion fuera de muestra de un partido real, verificada contra el
  resultado real conocido.
- ✅ **Mercados de tarjetas y corners** (Over/Under, Poisson sobre el total
  del partido) generados para partidos reales, ademas de los 5 de goles.
- ✅ Busqueda de partidos por equipo (`/matches?search=`), ordenada por
  proximidad real a hoy.
- ✅ Endpoint `/predictions/best`: mejores predicciones mezclando goles,
  tarjetas y corners sin depender de tener cuota de mercado.
- ✅ API FastAPI completa (16 endpoints), probada con `TestClient` end-to-end
  y sirviendo los modelos entrenados con datos reales.
- ✅ Dashboard Next.js (Upcoming agrupado por fecha con buscador y Top 5
  widget / Match detail con pestanhas Goals·Cards·Corners / Top Signals),
  verificado con capturas de pantalla contra datos reales.
- ✅ 49 tests automatizados pasando (`pytest backend/tests`); los tests usan
  datos sinteticos deliberadamente (deterministas, sin red), separados del
  dataset real usado para entrenar/predecir.

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
