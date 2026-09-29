# Modelado

## Modelos de goles comparados (`models/goals/`)

| Modelo | Enfoque | Notas |
|---|---|---|
| `baseline.py::LeagueAverageBaseline` | Poisson con lambda = media de la competicion | Ignora quien juega. Listón minimo obligatorio (seccion 52). |
| `poisson_model.py::PoissonRegressionModel` | 2x `sklearn.PoissonRegressor` (local/visitante) sobre features | Asume independencia entre goles local/visitante. |
| `dixon_coles.py::DixonColesModel` | Dixon & Coles (1997): attack/defense por equipo + home advantage + correccion `rho` en marcadores bajos | Interpretable, con decaimiento temporal (partidos recientes pesan mas). |
| `ml_classifier.py::MarketClassifierModel` | Un `LogisticRegression` por mercado, directo sobre features | No pasa por una distribucion de goles intermedia. |

Todos implementan (o se combinan a traves de) la misma interfaz de salida:
una distribucion de goles 0..5+ por equipo (`models/base.py::GoalsModel`), o
directamente una probabilidad de mercado (`MarketClassifierModel`). La
conversion distribucion -> probabilidad de mercado vive en UN solo sitio
(`prediction/probability.py`) para que todos los modelos se comparen de
forma identica.

## Por que Dixon-Coles y no solo Poisson

Poisson independiente subestima la probabilidad de marcadores bajos
correlacionados (0-0, 1-0, 0-1, 1-1). Dixon-Coles corrige exactamente esas 4
celdas con el parametro `rho`, ajustado por maxima verosimilitud junto con
attack/defense. Ver `DixonColesModel.predict_score_matrix`.

## Ensemble (`models/ensemble/ensemble.py`)

```
p_ensemble = w * p_statistical + (1 - w) * p_ml
```

`w` **no esta fijado a mano**: se busca por grid search en un set de
validacion separado del train, minimizando Log Loss
(`learn_ensemble_weight`). El peso aprendido se persiste junto al
`ModelVersion` (`hyperparameters`/artifact joblib).

## Calibracion (`models/calibration/`)

- **Metricas** (`metrics.py`): Brier Score, Log Loss, Expected Calibration
  Error, curva de fiabilidad con tamanho de muestra por bucket. Accuracy se
  calcula solo como referencia secundaria, nunca como criterio de seleccion.
- **Calibradores post-hoc** (`calibrators.py`): Isotonic Regression y Platt
  Scaling, siempre ajustados en un split de validacion DISTINTO del usado
  para medir la mejora despues (`backtesting/calibration.py::compare_calibration`).

## Confianza vs Edge vs Data Quality (seccion 72)

Tres numeros con significado distinto, nunca mezclados:

- **model_probability**: la probabilidad estimada, punto.
- **edge**: `model_probability - market_probability` (sin vig cuando es
  posible). Puede ser grande y venir de un modelo mal calibrado.
- **confidence** (`prediction/confidence.py`): combinacion documentada de
  calidad de calibracion historica + tamanho de muestra + acuerdo entre
  modelos + calidad de dato + **cobertura de mercado** (cuantas casas de
  apuestas independientes respaldan `market_probability`, ver
  `market/consensus.py` — una cuota de una unica casa pesa menos que un
  consenso de 5+). Un edge enorme con confidence baja se presenta como
  tal, no se disfraza.
- **data_quality** (`prediction/data_quality.py`): completitud +
  frescura + tamanho de muestra de ESTA prediccion concreta. Independiente
  de si el modelo acierta.

## Consenso de mercado, no una unica casa (`market/consensus.py`)

Con The Odds API llegan cuotas de VARIAS casas de apuestas por partido.
Tratar la cuota de una unica casa (aunque sea la "preferida") como "el
mercado" es fragil: una casa con un error de tipeo, una linea mal
identificada, o simplemente poco liquida, puede inflar un edge que en
realidad es ruido de datos, no una oportunidad real.

`compute_market_consensus()`:
1. Nunca mezcla `snapshot_type` distintos (pre_match/closing/opening/live)
   en el mismo calculo.
2. Quita el vig por bookmaker cuando cotiza ambas selecciones.
3. Descarta bookmakers outlier (probabilidad implicita a mas de 3.5
   desviaciones absolutas medianas de la mediana del grupo) ANTES de
   agregar — solo con 3+ casas, donde hay base estadistica para decidirlo.
4. Agrega con la MEDIANA de las probabilidades supervivientes.
5. Guarda `bookmakers_count`/`bookmakers_used`/`min_odds`/`max_odds`/
   `median_odds`/`average_odds` en cada `Prediction`, para poder mostrar
   evidencia de mercado y para que `confidence` la pondere.

## Consistencia entre mercados mutuamente excluyentes (2026-09-20)

`prediction/market_labels.py::MUTUALLY_EXCLUSIVE_GROUPS` (`home_win`+
`draw`+`away_win`; `over_2_5`+`under_2_5`) se fuerza a sumar 1 fila a fila
DESPUES de mezclar el ensemble estadistico+ML
(`renormalize_mutually_exclusive_groups`, llamado desde
`predictor.py::predict_markets_for_table` y desde `services/model_service.py`
antes de ajustar los calibradores).

**Por que hacia falta**: `MarketClassifierModel` entrena un
`LogisticRegression` INDEPENDIENTE por mercado, y `learn_ensemble_weight`
aprende un peso ML tambien independiente por mercado — nada garantiza que
`over_2_5` y `under_2_5` (o `home_win`/`draw`/`away_win`) sigan sumando 1
tras la mezcla, aunque la probabilidad puramente estadistica (derivada de
UNA sola matriz de marcador conjunta) si lo hiciera por construccion. Raiz
del problema y limites del fix documentados en detalle en
`docs/architecture_audit.md` (seccion 2): es una proyeccion sobre el
simplex de probabilidad, matematicamente principiada y no un parche visual
que "esconde" una de las dos señales — pero la correccion arquitectonica
completa (un clasificador multinomial conjunto en vez de N binarios
independientes) queda pendiente como trabajo futuro.

Test de regresion: `backend/tests/unit/test_market_consistency.py`
(reproduce el ejemplo literal del brief, Over 2.5=80%/Under 2.5=70%
simultaneos, con un modelo ML de mentira, y confirma que tras el fix la
suma servida es 1.0).

## Ranking de senhales (`prediction/ranking.py`)

Dos fases separadas: un filtro DURO configurable (`MIN_SIGNAL_ODDS`,
`MIN_EDGE_PP`, `MIN_BOOKMAKERS`, `MIN_DATA_QUALITY`, `MAX_SIGNAL_ODDS` en
`Settings`, nunca hardcodeados en el frontend) que descarta predicciones
sin mercado valido o con datos insuficientes, y un SCORING transparente
entre las que pasan:

```
signal_score = model_probability^2 * max(edge, 0) * confidence * data_quality
             * market_quality_multiplier * market_track_record
```

El cuadrado de la probabilidad es deliberado: sin el, una jugada mediocre
con mucho edge en puntos porcentuales (p.ej. 55% a cuota 3.0) puede
puntuar por encima de una jugada solida de alta probabilidad (p.ej. 80% a
cuota 1.35) solo por el tamanho bruto del edge.

**Factores anhadidos el 2026-09-20** (antes solo probabilidad/edge/
confidence/data_quality):
- `market_quality_multiplier` (HIGH=1.0/MEDIUM=0.85/LOW=0.65,
  `prediction/market_quality.py`): antes, "cuantas casas respaldan la
  cuota" solo entraba diluido dentro de `confidence` (uno de 5 componentes
  al 20%, sin mirar dispersion entre casas). Ahora es un factor propio y
  explicito, para que una cuota outlier de una sola casa discrepante no
  pueda ganar el ranking solo por probabilidad/edge nominales altos
  (seccion 18 del brief: anomalia OUTLIER).
- `market_track_record` (parametro opcional de `signal_score`, por defecto
  1.0 = neutro): pensado para el rendimiento historico observado de ESE
  tipo de mercado. Todavia no cableado automaticamente en el pipeline de
  generacion — requiere el pipeline de settlement/historico completo (ver
  `docs/architecture_audit.md`, seccion 6) para calcularlo de forma
  continua; el parametro existe y esta testeado para no tener que
  rediseñar la formula cuando se conecte.
- `calibrated_probability` se guarda (columna nueva en `Prediction`) pero
  **no** sustituye a `model_probability` dentro de esta formula: adoptarla
  como la probabilidad que dirige edge/ranking requiere validar
  out-of-sample que mejora el ranking real, no solo el Brier score del
  propio holdout de calibracion (circular si se usa para lo mismo).
  Decision pendiente, documentada, no un olvido.

`/predictions/best` y `/predictions/top-signals` ("Mejores señales") usan
este mismo scoring; `/predictions/model-only` expone, sin competir en el
ranking, las predicciones sin mercado (goles sin cuota todavia, o
tarjetas/corners si API-Football no esta configurado — ver
docs/data_sources.md, con esa fuente activa tarjetas/corners tambien
pueden tener mercado real y competir con normalidad).

**`MAX_SIGNAL_ODDS` (2026-09-18, feedback real de usuario)**: unica
excepcion deliberada a "el filtro duro no juzga calidad de la senhal, solo
que los datos sean validos". Un edge grande en un resultado muy
improbable (p.ej. modelo ve ~12% de probabilidad, cuota justa 8, el
mercado ofrece 15) es matematicamente un edge real, pero no es una
prediccion practica para destacar: sigue siendo mas probable que falle
que que acierte, y el `model_probability^2` del scoring no basta por si
solo para dejarlo fuera del top-N si un dia hay pocas senhales
candidatas (compite igual por "hueco" en la lista aunque su score
absoluto sea bajo). Por defecto `6.0` (cuotas por encima implican
probabilidad implicita < ~17%) — ajustable a tu propio criterio de
riesgo, o `None` para desactivarlo.

**`MIN_SIGNAL_ODDS=1.45` (2026-09-19, bug real corregido)**: antes era
`1.01`, lo que en la practica no excluia nada (cualquier cuota > 1.0 se
consideraba "valida"). Una cuota 1.02 (modelo ~98%, edge practicamente
nulo) SOLO quedaba penalizada por el scoring (`model_probability^2 *
edge`), no excluida — si un dia no habia ninguna otra senhal candidata,
esa cuota irrisoria podia colarse igual en el top-N, exactamente el
mismo problema (simetrico) que resuelve `MAX_SIGNAL_ODDS` en el extremo
alto. Con `1.45`, una cuota 1.02 se descarta en el filtro DURO, no solo
en el scoring.

**`MARKET_QUALITY_*` (`prediction/market_quality.py`)**: clasificacion
HIGH/MEDIUM/LOW de la EVIDENCIA DE MERCADO detras de una cuota (cuantas
casas + su dispersion), deliberadamente SEPARADA de `confidence`
(`prediction/confidence.py`, que mide fiabilidad del MODELO — calibracion,
tamanho de muestra, acuerdo entre modelos, y SI incluye `market_coverage`
como uno de sus 5 componentes, pero mezclado con el resto). Una senhal
respaldada por 1 sola casa y otra por 20+ pueden compartir el mismo
`confidence` si el resto de factores coincide; `market_quality` aisla esa
diferencia para que sea visible por si sola en la UI. Formula:

```
dispersion_ratio = (cuota_max - cuota_min) / cuota_mediana
HIGH:   bookmakers_used >= MARKET_QUALITY_HIGH_MIN_BOOKMAKERS (10)
        Y dispersion_ratio <= MARKET_QUALITY_MAX_DISPERSION_RATIO (0.15)
MEDIUM: bookmakers_used >= MARKET_QUALITY_MEDIUM_MIN_BOOKMAKERS (4),
        o >= el minimo de HIGH pero con demasiada dispersion
LOW:    resto de casos (incluido sin mercado)
```

**Frescura de cuotas (`MAX_ODDS_AGE_MINUTES`)**: `Prediction.created_at`
es el momento en que se genero esa prediccion concreta, capturando el
`market_probability`/`market_odds` vigentes en ese instante (no se
inventa una fecha de "actualizacion" aparte) — `odds_age_minutes` en la
respuesta de la API es siempre visible; `MAX_ODDS_AGE_MINUTES` (`None`
por defecto, sin excluir nada) activa la exclusion dura
(`ExclusionReason.STALE_ODDS`) si se fija un entero.

## Jornada actual (`services/round_service.py`)

La UI principal muestra la jornada en curso de cada liga, no simplemente
"los proximos partidos que haya": mientras queden partidos SIN JUGAR de
la jornada N, esa sigue siendo la jornada actual aunque la jornada N+1 ya
tenga fecha. El numero de jornada viene de `Match.matchday`, poblado solo
por el adapter de fixtures (openfootball, campo `round: "Matchday N"`,
verificado real y consistente en las 5 ligas del MVP) — el dataset
historico no trae jornada. Si un partido programado no tiene `matchday`
todavia (fixtures ingeridos antes de este cambio), se cae a un fallback
explicito por fecha en vez de fingir una jornada real.

**`get_next_round()`**: misma logica, para la jornada INMEDIATAMENTE
posterior a la actual — `None` si la actual es un fallback (no se puede
inventar una "siguiente jornada" sin matchday real) o si la actual es la
ultima programada.

**`scope` en `/predictions/top-signals`, `/best`, `/best/debug` y
`/model-only`** (2026-09-19, correccion central de esta revision): por
DEFECTO estos endpoints ahora usan `scope=current_round`, que restringe la
consulta SQL a los `match_ids` de la jornada actual de CADA competicion
(union de las 5 ligas del MVP, o solo una si se pasa `competition_code`)
— nunca una ventana de dias generica. Antes, el default era `days=4`
(una ventana relativa a "ahora"): un partido de la jornada siguiente
podia caer dentro de esos 4 dias y aparecer en "Mejores señales" solo por
tener mas edge, exactamente el bug reportado. `scope=next_round` para la
jornada siguiente; `scope=all_upcoming` (+ `days`) para volver al
comportamiento anterior explicitamente; `date` (un dia concreto) sigue
teniendo prioridad maxima sobre `scope` en cualquier caso.

## Cerrar el ciclo: resultados reales -> reentrenar -> evaluar

El modelo nunca mantiene estado entre ejecuciones: cada `football-edge
train` reentrena TODO desde cero (Dixon-Coles/baseline/ML classifier)
leyendo TODOS los partidos ya jugados de la BD en ese momento. Por eso
"que el modelo aprenda de la realidad" no necesita ninguna infraestructura
de aprendizaje online -- solo necesita que los resultados reales de la
jornada que acaba de terminar lleguen a la BD antes de reentrenar.

**Bug real corregido (2026-09-18)**: `ClubFootballMatchDataProvider`
(fuente por defecto de `football-edge update`) descarga el CSV historico
UNA VEZ y lo cachea en disco (`data/external/`, ~45MB). El parametro
`force_refresh` para invalidar ese cache existia desde el principio del
proyecto, pero NADA lo invocaba nunca en `True` -- asi que, una vez
descargado el CSV la primera vez, re-ejecutar `update` (o `refresh`, que
lo incluye) NUNCA volvia a comprobar si habia partidos nuevos jugados,
por muchas veces que se ejecutara. Esto bloqueaba silenciosamente
exactamente el flujo que se pedia: "cuando acabe la jornada, recopilar
los datos reales". Corregido con `ClubFootballMatchDataProvider.refresh_cache()`,
invocado por defecto en `football-edge update` (desactivable con
`--no-refresh-cache` solo para pruebas repetidas el mismo dia sin gastar
red).

**Flujo recomendado tras acabar una jornada** (ya encadenado en
`football-edge refresh`, en este orden):
1. `update` -- trae los resultados reales (fuerza el refresco del cache).
2. `evaluate` -- compara las predicciones que YA estaban guardadas de esos
   partidos (hechas ANTES del pitido inicial, nunca se borran al
   finalizar el partido) contra el resultado real: Brier score, log loss,
   calibracion y accuracy siempre; ROI hipotetico solo si habia cuota de
   mercado real (ver `backtesting/metrics.py::market_strategy_metrics`).
   Escribe `data/processed/evaluation_report.json`. Es puramente
   observabilidad -- no cambia nada del modelo, solo permite VER como de
   bien predijo antes de decidir si hace falta ajustar algo.
3. `train` -- reentrena con los resultados nuevos ya incorporados.
4. `predict-upcoming` -- genera predicciones para los proximos partidos
   con el modelo ya actualizado.

Comando aislado: `football-edge evaluate --competition laliga` (o sin
`--competition` para todas). Ver `services/evaluation_service.py`.

## Versionado (seccion 34)

Cada `train_competition_models(...)` crea una fila nueva en `model_versions`
(nunca sobrescribe) con: features usadas, hiperparametros, metricas de
holdout, `dataset_version`, y la ruta al artefacto `joblib` con los objetos
Python de los modelos entrenados + pesos de ensemble.

## Comportamiento historico por rango de edge (`backtesting/metrics.py::performance_by_edge_bucket`)

"NO asumas que mayor edge = mejor" (pedido explicito de usuario): esta
funcion agrupa las predicciones OUT-OF-FOLD del backtest walk-forward en
buckets de edge (0-5pp, 5-10pp, 10-15pp, 15-20pp, 20+pp) y calcula, POR
BUCKET, hit rate real, Brier score y ROI hipotetico. Se calcula sobre la
cuota CRUDA del mercado (sin quitar vig), el mismo criterio que usa
`market_strategy_metrics` para decidir "cuando se apuesta" — asi el
bucket de edge coincide exactamente con lo que la simulacion de ROI
considera una oportunidad. Se incluye automaticamente en el JSON de
`football-edge backtest` cuando hay cuotas de mercado disponibles
(`summary["performance_by_edge_bucket"]`). Si un bucket de edge alto no
muestra mejor ROI/hit-rate que uno bajo, eso es evidencia REAL de que ese
edge no era fiable (cuota mal identificada, modelo mal calibrado para esa
zona, muestra pequenha...), nunca una suposicion.

## Calibracion: raw vs calibrada (`backtesting/calibration.py`)

`compare_calibration()` (Platt/isotonic) existia desde el principio del
proyecto pero **nada la invocaba** en ningun sitio — bug real corregido
(2026-09-20). `calibration_report_for_table()` es el punto de entrada
real: usa las probabilidades OUT-OF-FOLD del backtest walk-forward (nunca
probabilidades de un modelo que ya vio esos partidos en entrenamiento,
que sesgarian el "antes" a favor de parecer ya bien calibrado). Comando:

```
football-edge calibration-report --competition laliga --market over_2_5 --method isotonic
```

Escribe un JSON con Brier/log loss/ECE antes y despues de calibrar sobre
un split de test disjunto del usado para ajustar el calibrador. **Las
predicciones en produccion (`prediction_service.py`) usan SIEMPRE la
probabilidad CRUDA del modelo para el edge** — este comando es solo
diagnostico. Si demuestra una mejora real y consistente en varias
competiciones/mercados, esa seria la evidencia necesaria para plantear
activar calibracion en produccion (nunca se activa automaticamente sin
esa evaluacion previa, tal y como se pidio explicitamente).

## Filtros adicionales de "Mejores señales" (`min_edge`, `min_bookmakers`, `quality`)

Sobre el filtro DURO de calidad (`evaluate_quality_gate`, que decide QUE
entra) y el `scope`/`competition_code` (que deciden que PARTIDOS se
consideran), `/predictions/top-signals` y `/predictions/best` aceptan tres
ajustes finos adicionales pensados para sliders/selectores de frontend:

- `min_edge`: edge minimo en PUNTOS PORCENTUALES (ej. `5` = 5pp). Distinto
  de `Settings.min_edge_pp` (que pese al nombre es una fraccion 0-1 y es
  el umbral del filtro duro, no ajustable por request).
- `min_bookmakers`: numero minimo de casas respaldando el consenso.
- `quality`: `HIGH`/`MEDIUM`/`LOW`/`ALL` (por defecto), sobre
  `prediction/market_quality.py`.

Ninguno de los tres sustituye al filtro duro: son un afinado adicional
sobre lo que ya lo paso.

## Detalle de una señal (`GET /predictions/{id}/detail`)

Desglose bookmaker-por-bookmaker (cuota, diferencia respecto al consenso,
si se descarto como outlier — ver `market/consensus.py`) mas una
explicacion en lenguaje llano de por que aparece la senhal
(`explanation_summary`), construida SOLO con numeros reales trazables a
la respuesta (probabilidad del modelo, probabilidad de mercado, edge,
cuota, cuota justa, calidad de mercado, antigueedad de la cuota, calidad
de datos, confianza). Nunca usa lenguaje de certeza ("apuesta segura",
"ganadora", "100%") — es una herramienta de analisis estadistico, no una
promesa de resultado. Frontend: `/signals/[id]`.

## Agente "Jarvis" (`backend/app/agent/`, Fase 1-2, MVP)

Capa de agente conversacional CONSTRUIDA ENCIMA del motor existente, no un
reemplazo: el LLM (Anthropic, `agent/llm.py`, interfaz `LLMClient`
abstraida para poder cambiar de proveedor por configuracion) nunca calcula
probabilidades ni inventa datos — solo decide que "tool" llamar
(`agent/tools.py`) entre las herramientas registradas, cada una un wrapper
fino sobre un servicio que YA EXISTE, e interpreta/resume el resultado en
lenguaje natural (`agent/orchestrator.py` implementa el bucle
LLM -> tool_use -> resultado -> LLM, con limite de iteraciones y timeout
por tool para que nunca quede colgado).

MVP: una sola tool, `get_matches_today` (partidos de la jornada actual de
cada competicion, mismo filtro de fecha que el Home -- ver
`services/round_service.py` -- con la MEJOR PREDICCION de cada partido via
`prediction/anomaly.py::best_prediction_per_match`, el mismo criterio de
`signal_score` que usa el resto de la app, nunca "la probabilidad mas
alta" sin mas). Anhadir una tool nueva no requiere tocar el orquestador:
solo escribir su handler y anhadirla a `TOOLS` en `agent/tools.py`.

Endpoint: `POST /agent/chat` (`backend/app/api/routes/agent.py`), body
`{message, history}` — la memoria de conversacion es SIN PERSISTENCIA en
servidor para este MVP (el frontend reenvia el historial completo en cada
request); memoria de largo plazo/de dominio queda para una fase
posterior. Desactivado por defecto (`AGENT_ENABLED=false`): sin
`AGENT_SHARED_SECRET` configurado (y, con el proveedor "anthropic", sin
`ANTHROPIC_API_KEY`), responde 503 en vez de quedar abierto. Autenticacion:
secreto compartido en la cabecera `X-Agent-Key` (uso personal, sin
multiusuario — pedido explicito de usuario; si algun dia se abre a mas
gente, esto debe evolucionar a un sistema de auth real).

### Dos proveedores de LLM (`AGENT_LLM_PROVIDER`)

- **`anthropic`** (por defecto): `AnthropicLLMClient`, API de Anthropic
  facturada por token (necesita `ANTHROPIC_API_KEY` de pago).
- **`claude_code`**: `ClaudeCodeLLMClient`, usa el Claude Agent SDK (el
  mismo motor de Claude Code) con el CLI `claude` logueado localmente via
  suscripcion Claude Pro/Max — sin pagar API aparte (pedido explicito de
  usuario: "no quiero pagar por la api key"). Diferencia de arquitectura
  importante: aqui el SDK gestiona el bucle de tool-calling EL SOLO (via
  un servidor MCP en proceso que expone las mismas `TOOLS` de
  `agent/tools.py`), asi que `run_turn()` para este proveedor siempre
  devuelve `tool_calls=[]` al orquestador — ya se resolvio todo dentro.
  Las llamadas a tools se siguen logueando (mismo evento
  `agent.tool_call`) para observabilidad, pero no aparecen en el
  `tool_log` de la respuesta HTTP para este proveedor (limitacion
  documentada, no un descuido).

  **Seguridad**: se pasa `tools=[]` a `ClaudeAgentOptions`, lo que
  desactiva TODAS las tools nativas de Claude Code (Bash, Read, Write,
  WebFetch...) — el agente solo puede llamar a las tools de
  `agent/tools.py` expuestas via MCP, nunca puede ejecutar comandos del
  sistema ni tocar el filesystem (seccion 17 del brief).

  **Docker**: `backend/Dockerfile` instala Node.js/npm y el CLI `claude`
  (`npm install -g @anthropic-ai/claude-code`) en la imagen. Como
  `claude login` es un flujo interactivo (abre navegador) que no encaja
  dentro de un contenedor, la autenticacion ahi es via
  `CLAUDE_CODE_OAUTH_TOKEN` (variable de entorno, generada una vez en tu
  propia maquina con `claude setup-token` y pegada en tu `.env` — ver
  `.env.example`): `docker-compose.yml` ya inyecta el `.env` completo a
  `backend`/`scheduler` (`env_file`), asi que el CLI dentro del contenedor
  la coge sola, sin login interactivo. Un `RuntimeError` de
  `ClaudeCodeLLMClient` (CLI ausente, token caducado/invalido) se traduce
  a un 502 con detalle legible en `POST /agent/chat`, nunca a un 500 sin
  cuerpo (error real corregido tras probarlo: el traceback quedaba sin
  capturar en la ruta, "Expecting value" en el cliente).

  **Limite de uso**: el plan Pro/Max esta pensado para uso interactivo, no
  para un servicio en segundo plano con mucho trafico — si Jarvis se usa
  intensivamente, puedes toparte con el limite de tu plan antes que con
  el coste de la API de pago (que no tiene techo salvo el que tu pongas).

  **`IS_SANDBOX=1`**: el contenedor Docker corre como root (no hay ningun
  `USER` no-root en el Dockerfile), y Claude Code se niega a usar
  `bypassPermissions` como root salvo que se le diga explicitamente que
  esta en un entorno aislado (si no, falla con "--dangerously-skip-
  permissions cannot be used with root/sudo privileges" -- error real
  reproducido probando esto en Docker). `ClaudeCodeLLMClient` pasa
  `env={"IS_SANDBOX": "1"}` al proceso del CLI para esto -- no relaja
  ninguna seguridad real, el aislamiento de verdad ya lo da `tools=[]`
  (sin Bash/Read/Write nativos).

### Fase 4 -- interfaz de chat

Pagina `/jarvis` (frontend) con chat simple, sin memoria en servidor
(reenvia el historial completo en cada request). Llama a un proxy propio
del frontend, `frontend/app/api/agent/chat/route.ts` (server-side), en vez
de al backend directamente desde el navegador: asi `AGENT_SHARED_SECRET`
nunca se manda como `NEXT_PUBLIC_*` (quedaria visible para cualquiera que
abra las herramientas de desarrollador de la pagina). `docker-compose.yml`
pasa ese mismo secreto al contenedor `frontend` (variable de entorno
normal, no de build) para que el proxy lo pueda usar.

### Fase 5 -- mas tools (`analyze_match`, `get_model_performance`)

Dos tools nuevas, mismos principios que `get_matches_today` (wrapper fino
sobre logica YA EXISTENTE, nunca inventa datos):

- **`analyze_match(query)`**: busca un partido por nombre de uno o los dos
  equipos (`_find_match`, MISMA logica de busqueda por substring que
  `GET /matches?search=` -- nunca similitud difusa/ambigua, principio ya
  establecido en `normalization/teams.py`). Devuelve TODAS las
  predicciones del partido (no solo la mejor), cada una con sus factores
  reales (`explanation.factors`, el mismo campo que ya expone
  `/predictions/{id}/detail` -- nunca un factor inventado que el modelo no
  pueda respaldar), la mejor prediccion, y el resultado real si el partido
  ya termino. Cubre el ejemplo destacado del brief: "analiza el Barcelona -
  Getafe".
- **`get_model_performance(competition_code?, market?)`**: delega
  directamente en `services/evaluation_service.py::real_performance_report`
  (ya usado por `GET /models/performance`) -- cero logica nueva, solo
  expone el mismo informe real (accuracy/Brier/calibracion/ROI sobre
  predicciones YA liquidadas) al agente.

### Fase 6 -- alertas proactivas (`backend/app/agent/alerts.py`)

`generate_alerts_for_current_round(db)` escanea la jornada actual de cada
competicion vigilada y guarda filas en la tabla nueva `agent_alerts`
cuando `prediction/anomaly.py` (ya usado por /top-signals) detecta algo
digno de destacar:

- Los 4 tipos de anomalia ya existentes (`CONTRADICCION`,
  `SENAL_BAJA_FIABILIDAD`, `OUTLIER`, `HIGH_PROBABILITY_LOW_VALUE`) --
  una alerta por (partido, tipo), nunca una por prediccion individual.
- `SENAL_DE_VALOR` (nueva, propia de este modulo): la mejor prediccion del
  partido (`best_prediction_per_match`, sin contradiccion) cuando su edge
  y su `signal_tier` superan los umbrales configurados
  (`AGENT_ALERT_MIN_EDGE_PP`, `AGENT_ALERT_MIN_TIER` -- reusa la MISMA
  clasificacion HIGH/MEDIUM/LOW de `prediction/confidence.py` en vez de
  inventar un segundo umbral de "confianza").

Idempotente por diseno: `UniqueConstraint(match_id, alert_type)` en el
modelo `AgentAlert` (`db/models/modeling.py`) impide duplicar la misma
alerta aunque se genere cada hora (se ejecuta desde `football-edge
evaluate`, el mismo paso que ya liquida partidos terminados cada hora via
el scheduler -- asi no hace falta tocar `docker-compose.yml` cada vez que
se anhada un nuevo tipo de alerta). Desactivado por defecto
(`AGENT_ALERTS_ENABLED=false`, seccion 13 del brief: "quiero que esto sea
configurable", nunca un ON silencioso).

**Entrega deliberadamente NO incluida**: envio real (email/Telegram/push).
No hay canal de notificacion configurado ni credenciales pedidas por el
usuario -- inventar uno seria fabricar infraestructura sin que nos la
hayan pedido. La tool `get_active_alerts` es, de momento, la unica forma
de "recibir" estas alertas: preguntandole a Jarvis ("¿hay alguna
alerta?").

### Fase 7 -- voz (`backend/app/agent/transcription.py`, `POST /agent/transcribe`)

Primer intento: la Web Speech API nativa del navegador
(`webkitSpeechRecognition`) -- gratis, cero dependencias nuevas. Se
descarto tras un fallo real reportado por un usuario: esa API NO
transcribe localmente pese a las apariencias, manda el audio a un
servidor de reconocimiento de Google usando una clave API que Chrome trae
integrada de fabrica. **Brave (y cualquier Chromium centrado en
privacidad) elimina esa clave a proposito**, asi que el reconocimiento
falla siempre con `event.error === "network"` aunque el microfono en si
funcione perfectamente y haya conexion a internet normal -- reproducido
incluso con los Shields de Brave desactivados.

Solucion adoptada, la MISMA arquitectura que usa claude.ai para voz: el
navegador solo GRABA el audio (`MediaRecorder`, API distinta de
`SpeechRecognition` -- funciona en cualquier navegador, incluidos
Brave/Firefox) y lo manda a nuestro propio backend, donde
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2,
mas rapido que `openai-whisper` en CPU con precision equivalente) lo
transcribe localmente. Sin API key de pago ni depender de ningun servicio
externo de Google -- coherente con el mismo principio que llevo a elegir
`AGENT_LLM_PROVIDER=claude_code` (pagar con lo que ya tienes, no con una
API de terceros).

Flujo: `JarvisChat.tsx` graba con `MediaRecorder` -> sube el blob
(webm/opus normalmente) a `POST /api/agent/transcribe` (proxy Next.js,
mismo patron que `/api/agent/chat` -- reenvia el `FormData` tal cual,
nunca `request.text()`, para no corromper los bytes binarios del audio) ->
`POST /agent/transcribe` en el backend (mismo guardian de autenticacion
`X-Agent-Key`/`AGENT_SHARED_SECRET` que `/agent/chat`) -> `transcribe_audio()`
carga el modelo Whisper (perezoso, cacheado con `lru_cache`, tamanho
configurable via `WHISPER_MODEL_SIZE`) y devuelve el texto -> el frontend
rellena el input y lo envia como un mensaje de texto normal, sin ningun
camino especial en el orquestador ni en el LLM.

Modelo cacheado en `data/cache/whisper` (bajo el volumen `./data` ya
montado en `docker-compose.yml`) para no re-descargarlo en cada
`docker compose up`. `WHISPER_COMPUTE_TYPE=int8` acelera la inferencia en
CPU con perdida de precision minima para clips cortos.

**Precision con nombres propios** (bug real, 2026-09-29): con
`WHISPER_MODEL_SIZE=base`, "Bayern de Múnich" se transcribia como "Bayern
de Monoch". Dos cambios: (1) subir el modelo por defecto a `small`
(mejora sensible en nombres extranjeros manteniendo un tiempo de
inferencia razonable en CPU); (2) pasar un `initial_prompt` fijo a
faster-whisper con una lista CURADA de clubes extranjeros con
transliteracion ambigua al hablarlos en espanhol (`_TEAM_NAME_PROMPT` en
`transcription.py`) -- curada a mano y corta a proposito: Whisper trunca el
prompt a ~224 tokens, asi que volcar los ~140 nombres de
`normalization/teams.py::KNOWN_ALIASES` enteros se cortaria a mitad de
lista de forma impredecible; los nombres 100% espanholes ("Real Madrid",
"Barcelona") ya se reconocen bien sin pista y no hacia falta incluirlos.

### Voz de salida -- sintesis (`backend/app/agent/tts.py`, `POST /agent/speak`)

Mismo patron que la transcripcion, mismo motivo real: `speechSynthesis`
nativa del navegador devuelve **0 voces** en Linux
(`speechSynthesis.getVoices().length === 0`, confirmado por un usuario) --
las voces de calidad de Chrome son remotas y dependen del mismo servicio
de Google que Brave/Chromium en Linux no tiene disponible. Solucion:
[Piper](https://github.com/rhasspy/piper) (proyecto Rhasspy) sintetiza
localmente en CPU con voces neuronales de buena calidad.

Flujo: tras recibir la respuesta de `/agent/chat`, si "Leer las respuestas
en voz alta" esta marcado, `JarvisChat.tsx` manda el texto a
`POST /api/agent/speak` (proxy Next.js, reenvia la respuesta binaria con
`arrayBuffer()`, nunca `.text()`) -> `POST /agent/speak` en el backend
(mismo guardian `X-Agent-Key` que el resto de `/agent/*`) ->
`synthesize_speech()` carga la voz Piper (perezosa, cacheada con
`lru_cache`; se descarga sola la primera vez con
`piper.download_voices.download_voice`, cacheada en `data/cache/piper`
bajo el mismo volumen `./data`) y devuelve un WAV -> el frontend lo
reproduce con un elemento `<audio>` (no depende de ninguna voz del
sistema, a diferencia de `speechSynthesis`). `PIPER_VOICE` es
configurable (formato `<idioma>-<nombre>-<calidad>`, catalogo completo en
[VOICES.md](https://github.com/rhasspy/piper/blob/master/VOICES.md)).

### Enlaces directos a partidos desde el chat (`extract_match_references`)

Pedido real de usuario: "muestrame el partido del Real Madrid contra
Malaga". Jarvis no controla la navegacion del frontend (ni deberia
inventarsela), pero SI puede decir que partidos ha consultado de verdad --
`agent/tools.py::extract_match_references()` relee (nunca inventa) los
`match_id` que `analyze_match`/`get_matches_today` ya devolvieron, y
`AgentChatResponse.referenced_matches` los expone al frontend, que renderiza
un enlace "Ver partido: X vs Y →" bajo la respuesta.

Complicacion real: `ClaudeCodeLLMClient` (proveedor `claude_code`) resuelve
el bucle de tool-calling ENTERO dentro del SDK -- el orquestador
(`orchestrator.py::run_agent_turn`) nunca ve esos tool_use/tool_result uno
a uno para ese proveedor, a diferencia de `AnthropicLLMClient` (bucle
hecho a mano en el propio orquestador). Por eso `ClaudeCodeLLMClient`
expone un atributo `last_tool_results` (poblado dentro de
`_wrap_as_sdk_tool`, el mismo punto donde ya se loguea cada `agent.tool_call`)
que el orquestador lee con `getattr(llm, "last_tool_results", [])` despues
de cada `run_turn()` -- funciona para ambos proveedores sin acoplar el
orquestador a los detalles internos de ninguno.

### Conversacion continua manos libres (`JarvisChat.tsx::monitorSilence`)

Pedido real de usuario: "no tener que darle al boton de hablar todo el
rato". Un toggle "🔁 Conversación continua" arranca un bucle que graba,
transcribe, envia y vuelve a escuchar solo, sin ninguna otra interaccion.
Parar de grabar SIN que el usuario pulse nada requiere saber cuando ha
dejado de hablar: `monitorSilence()` analiza el volumen (RMS) del stream
del microfono via Web Audio API (`AnalyserNode`) y para la grabacion tras
~1.2s de silencio siguiendo a voz detectada (o un tope de 15s). Cada turno
espera a que `speak()` termine de sonar (o se resuelve de inmediato si el
navegador bloqueo el autoplay) antes de volver a escuchar, para minimizar
la posibilidad de que el microfono capte la propia voz de Jarvis saliendo
por los altavoces -- limitacion honesta: sin auriculares, ese eco SI puede
colarse en la siguiente transcripcion. Limitacion adicional del navegador:
la politica de autoplay solo concede audio automatico ligado al gesto que
activo el modo continuo (el primer turno); a partir de ahi, cada respuesta
hablada puede necesitar el boton "▶️ Reproducir respuesta" ya existente --
no hay forma de evitarlo desde JavaScript, es una decision del navegador.
