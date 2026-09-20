# Auditoria de arquitectura y hoja de ruta — "de detector de edge a plataforma de prediccion"

Fecha: 2026-09-20. Punto de partida: commit `7dbad98` (152 tests backend en
verde). Este documento es el Fase 1 (auditoria) pedido por el usuario, y
tambien registra que se implemento despues (Fase 2 parcial) y que queda
pendiente, para que una sesion futura pueda retomarlo sin releer todo el
codigo desde cero.

## 1. Como funciona hoy el sistema (auditoria)

**Fixtures**: `Match` (`backend/app/db/models/matches.py`) con `status`
scheduled|finished (no solo fecha), `matchday`, `home_goals`/`away_goals`
nullable hasta que se conoce el resultado. `MatchOdds` guarda cuotas por
bookmaker/mercado/linea/seleccion con `snapshot_type` (opening/closing).
`MatchStatistics` guarda estadisticas POST-partido, explicitamente marcadas
para no usarse como feature del mismo partido (anti-leakage).

**Modelos**: por competicion se entrena (`services/model_service.py`):
baseline (media de liga), Dixon-Coles (con correccion rho para marcadores
bajos), y un `MarketClassifierModel` (`models/goals/ml_classifier.py`) que
es, literalmente, **un `LogisticRegression` independiente por cada
market_key** (`over_1_5`, `over_2_5`, `under_2_5`, `btts`, `home_win`,
`draw`, `away_win`, `double_chance_*`, `*_team_over_*`) entrenado
directamente sobre las features, cada uno optimizando su propio log loss
sin ninguna restriccion conjunta entre mercados.

**Ensemble**: `models/ensemble/ensemble.py::learn_ensemble_weight` busca por
grid search, **por mercado y de forma independiente**, el peso `w` que
minimiza el log loss de `w * p_estadistico + (1-w) * p_ml` en un holdout.
No hay ninguna restriccion de que los pesos de mercados relacionados
(`home_win`/`draw`/`away_win`, o `over_2_5`/`under_2_5`) coincidan.

**Probabilidad de mercado (estadistica)**: `prediction/market_labels.py`
define TODOS los mercados de goles a partir de UNA UNICA matriz conjunta de
marcador (`probability_from_score_matrix`), particionando el mismo
`score_matrix` con una mascara booleana por mercado. Por construccion
matematica, cualquier grupo de mercados mutuamente excluyentes derivado asi
(`home_win`+`draw`+`away_win`, `over_2.5`+`under_2.5`) suma exactamente 1.

**Mercados y cuotas**: 1X2, Over/Under 1.5/2.5/3.5, BTTS, doble oportunidad
(derivada matematicamente de 1X2 sin vig, nunca una cuota inventada), goles
por equipo (sin cuota de mercado hoy, ninguna fuente la trae). Consenso de
mercado con mediana + deteccion de outliers por MAD
(`market/consensus.py`), quitado de vig (`market/vig.py`).

**Edge/fair odds**: `edge = model_probability - market_probability`
(`prediction/edge.py`), `fair_odds = 1/model_probability`
(`prediction/fair_odds.py`). Documentado en `docs/modeling.md` que un edge
grande NO es sinonimo de buena senhal por si solo — de ahi el
`signal_score` separado.

**Ranking**: dos fases (`prediction/ranking.py`): filtro duro
(`evaluate_quality_gate`, con `ExclusionReason` explicito — nunca una
senhal "desaparece" en silencio) + `signal_score` (soft ranking) para las
que pasan el filtro.

**Calidad de mercado vs confianza del modelo**: separados a proposito
(`prediction/market_quality.py` vs `prediction/confidence.py`) — el primero
mide evidencia de mercado (numero de casas + dispersion), el segundo mide
fiabilidad del modelo (calibracion historica, tamanho de muestra, acuerdo
entre submodelos, calidad de datos, cobertura de mercado).

**Historico/settlement (antes de este cambio)**: `PredictionResult` ya
existia (`outcome: bool`, `settled_at`), y
`services/evaluation_service.py::evaluate_settled_predictions` ya calcula
Brier/log loss/ROI hipotetico por mercado sobre predicciones YA GUARDADAS de
partidos YA finalizados — sin recalcular nada con el modelo actual. Lo que
faltaba: exponer esto como historico navegable (endpoints/frontend), y
completar el snapshot con `calibrated_probability`/`signal_score`
congelados y `actual_result` literal (ver seccion 3).

**Persistencia/migraciones**: sin Alembic. `db/database.py::init_db()` hace
`Base.metadata.create_all()` + `_sync_missing_columns()`, que anhade
columnas NUEVAS (nullable) a tablas ya existentes via `ALTER TABLE ADD
COLUMN`, sin tocar filas existentes ni borrar nada. Es el patron ya
establecido en este repo (documentado en el propio archivo) — este cambio
lo sigue en vez de introducir Alembic de golpe.

## 2. RAIZ del problema de inconsistencia entre mercados (verificado leyendo codigo, no asumido)

La hipotesis del usuario era correcta: **el culpable es
`MarketClassifierModel` + `learn_ensemble_weight`**, no un fallo de
normalizacion de cuotas ni de matching.

Cadena exacta:
1. `probability_from_score_matrix` (estadistico) es consistente por
   construccion: `home_win`+`draw`+`away_win`=1 y `over_2_5`+`under_2_5`=1
   siempre, porque salen de particionar la MISMA matriz conjunta.
2. `MarketClassifierModel.fit` entrena un `LogisticRegression` **por
   mercado, por separado**, cada uno viendo solo su propia etiqueta
   binaria. Nada le dice al clasificador de `over_2_5` que existe un
   `under_2_5` con el que debe sumar 1.
3. `learn_ensemble_weight` elige, **independientemente para cada
   market_key**, el peso `w` que minimiza SU log loss. En la practica los
   pesos de `over_2_5` y `under_2_5` (o de `home_win`/`draw`/`away_win`) casi
   nunca coinciden exactamente.
4. Resultado: `p_final = w*p_stat + (1-w)*p_ml` para cada mercado del grupo
   por separado, y la suma del grupo ya no es 1 en cuanto CUALQUIERA de los
   pesos ML del grupo es > 0 y los clasificadores discrepan del score matrix
   (que es la situacion normal, no un caso raro).

Esto se confirmo con un test end-to-end nuevo
(`backend/tests/unit/test_market_consistency.py::test_predict_markets_for_table_is_internally_consistent_despite_disagreeing_ml_model`)
que reproduce el ejemplo literal del brief (over_2.5=80%, under_2.5=70%) con
un `ml_model` de mentira y confirma que, SIN el fix, la suma es 1.50, no 1.0.

### Fix implementado (Fase 2)

`prediction/market_labels.py::MUTUALLY_EXCLUSIVE_GROUPS` +
`renormalize_mutually_exclusive_groups`: despues del blending del ensemble
(en `prediction/predictor.py::predict_markets_for_table`, ANTES de calcular
edge/fair_odds/explicacion), cada grupo mutuamente excluyente completo se
proyecta sobre el simplex (`p_i / sum(p_grupo)`), fila a fila. Esto:

- Es una operacion matematicamente principiada (proyeccion sobre el simplex
  de probabilidad), no "esconder una de las dos": conserva toda la
  informacion relativa del ensemble (cual seleccion es mas probable y por
  cuanto), solo restaura la restriccion de que son mutuamente excluyentes.
- Se aplica tambien en entrenamiento (`services/model_service.py`) antes de
  ajustar los calibradores, para que `calibrated_probability` se calibre
  sobre la MISMA distribucion que se sirve en produccion (si se calibrara
  sobre la version cruda, el numero guardado no correspondería a la
  probabilidad realmente usada para edge/ranking).
- Es un no-op cuando no hay modelo ML (`ml_model=None`) o cuando los pesos
  del ensemble son iguales entre los mercados de un grupo: en ese caso el
  input ya sumaba 1 y la renormalizacion no cambia nada (verificado por los
  tests).

**Limite honesto de este fix (no es la solucion completa)**: renormalizar
DESPUES de mezclar corrige el sintoma con un mecanismo principiado, pero la
causa arquitectonica de fondo (clasificadores binarios independientes en
vez de un modelo conjunto restringido al simplex) sigue ahi. La correccion
definitiva seria sustituir `MarketClassifierModel` por un clasificador
**multinomial conjunto** para cada grupo mutuamente excluyente (softmax
sobre las clases del grupo en vez de un sigmoid por clase), entrenado y
evaluado igual que el resto (mismo Brier/log loss walk-forward). Esto queda
como trabajo futuro documentado aqui (ver seccion 6), porque es un cambio
de modelo que requiere su propio backtest de validacion antes de
reemplazar el actual — no se hace a ciegas bajo presion de tiempo.

## 3. Cambios de esquema (snapshot inmutable, seccion 9 del brief)

Anhadido a `Prediction` (nullable, via el patron `_sync_missing_columns`
existente, sin Alembic):
- `calibrated_probability: float | None` — salida de un
  `IsotonicRegression` ajustado en el holdout de entrenamiento (nunca sobre
  los mismos datos), solo cuando hay >= `MIN_CALIBRATION_MATCHES` (60)
  partidos en el holdout para ese mercado; si no, `None` explicito (nunca
  inventado). Guardado ADEMAS de `model_probability` (no en su lugar): se
  decidio NO cambiar que probabilidad dirige edge/ranking (ver seccion 6,
  "decisiones deliberadamente no tomadas").
- `signal_score: float | None` — el `signal_score` calculado y CONGELADO en
  el momento de generar la prediccion (`services/prediction_service.py`),
  con los datos de mercado vigentes en ESE momento. Nunca se recalcula
  despues con pesos/umbrales mas nuevos.

Anhadido a `PredictionResult`:
- `actual_result: str | None` — pensado para el resultado literal
  (`"2-1"`, `"over"`, etc.), distinto del booleano `outcome` (que ya hacia
  de `is_correct`). **No cableado todavia** (ver seccion 6): el settlement
  pipeline que lo rellena es Fase 4, no completada en esta sesion.

`model_version_id` en `Prediction` y `ModelVersion.calibration_method` ya
existian; ahora `calibration_method` se rellena de verdad (`"isotonic"` o
`None`) en vez de estar siempre a `None`.

## 4. `signal_score`: formula completa (Fase 2, seccion 1 del brief)

Documentado en detalle en el docstring de `prediction/ranking.py::signal_score`
y en `docs/modeling.md`. Resumen:

```
signal_score = model_probability^2
             * max(edge, 0)
             * confidence
             * data_quality
             * market_quality_multiplier   # HIGH=1.0 / MEDIUM=0.85 / LOW=0.65
             * market_track_record          # [0,1], neutro=1.0 por defecto
```

- `model_probability^2`, `edge`, `confidence`, `data_quality`: formula
  original de la sesion anterior, sin cambios (ya pasaba los tests que
  validan sus propiedades: castiga probabilidad baja mas que edge en
  bruto, hunde a ~0 cuando el edge es ~0 aunque la probabilidad sea 98%).
- `market_quality_multiplier` (NUEVO): antes, "cuantas casas respaldan la
  cuota" solo entraba diluido dentro de `confidence` (uno de sus 5
  componentes ponderados al 20%) y SIN mirar dispersion entre casas. Ahora
  `market_quality_tier` (HIGH/MEDIUM/LOW, que SI mira dispersion) es un
  factor propio y explicito del `signal_score`, para que una cuota
  respaldada por 1 sola casa discrepante no pueda ganar el ranking de
  "mejor senhal" solo por probabilidad/edge nominales altos.
- `market_track_record` (NUEVO, parametro opcional): pensado para el
  rendimiento historico observado de ESE tipo de mercado (constraint del
  brief: "historico de ese tipo de mercado"). Por defecto 1.0 (neutro: un
  mercado sin historico suficiente no se penaliza por falta de dato). **No
  esta cableado automaticamente en el pipeline de generacion todavia** —
  requiere que el pipeline de settlement (Fase 4) este completo para poder
  calcular calibracion historica por mercado de forma continua. El
  parametro existe y esta testeado
  (`test_signal_score_market_track_record_defaults_to_neutral`) para que
  conectar esto despues sea cambiar una llamada, no rediseñar la formula.

Deliberadamente NO se uso `calibrated_probability` en vez de
`model_probability` dentro del `signal_score`/edge: adoptar la probabilidad
calibrada como la que dirige el ranking es una decision de producto que
requiere validar OUT-OF-SAMPLE que mejora el ranking real (no solo que
mejora el Brier score en el holdout de calibracion, que es circular si se
usa para lo mismo). Se documenta como decision pendiente, no como olvido.

## 5. Auditoria de disponibilidad de datos por mercado (seccion 26 del brief)

| Mercado | Historico | Cuotas | Modelo disponible | Muestra | Estado |
|---|---|---|---|---|---|
| 1X2 (home/draw/away) | ALTA (Bet365 en dataset historico + h2h de The Odds API) | ALTA | SI (Dixon-Coles + score matrix + ML) | Big5, miles de partidos/temporada | **CORE** |
| Over/Under 2.5 goles | ALTA (misma fuente, linea principal) | ALTA | SI | igual | **CORE** |
| Over/Under 1.5 / 3.5 goles | ALTA (derivable del mismo dataset) | MEDIA (`alternate_totals`, anhadido despues, sin verificar en produccion aun segun `docs/data_sources.md`) | SI | igual | **CORE** (goles), cuota con menor cobertura que la linea 2.5 |
| BTTS | ALTA (derivable de goles reales) | MEDIA (mercado `btts` de The Odds API, anhadido despues, sin verificacion end-to-end en produccion todavia) | SI | igual | **CORE** |
| Doble oportunidad (1X/X2/12) | ALTA (derivada matematicamente de 1X2, no depende de una fuente propia) | ALTA (derivada del no-vig de h2h, nunca None si hay h2h) | SI | igual | **CORE** |
| Goles por equipo (home/away over 0.5/1.5) | ALTA (goles reales por equipo) | **NINGUNA fuente actual la trae** | SI (modelo, sin cuota) | igual | **FUTURE** para señal de mercado; ya vive en `/predictions/model-only` |
| Asian Handicap | — | No integrada | No | — | **FUTURE** (Prioridad 2, sin trabajo empezado) |
| Draw No Bet | — | No integrada (derivable matematicamente igual que doble oportunidad, en cuanto se decida priorizar) | Derivable del score matrix | — | **FUTURE** (Prioridad 2, tecnicamente trivial de anhadir, no priorizado esta sesion) |
| Corners O/U (total y por equipo) | MEDIA (dataset historico SI trae corners; ver `MatchStatistics`) | **Aparcado por decision de producto (2026-09-18)**: unica fuente viable (API-Football) exige plan de pago | Parcial (`TotalCountPoissonModel` YA EXISTE y entrena, `models/ensemble` no lo cubre) | Big5 | **FUTURE** (modelo listo, sin cuota real fiable hoy) |
| Cards O/U (total y por equipo) | MEDIA (igual, dataset historico trae tarjetas) | Igual que corners: aparcado | Igual que corners | Big5 | **FUTURE** |

Ninguna decision de esta sesion contradice esta tabla: no se ha
implementado Asian Handicap/Draw No Bet/Corners/Cards como señales reales,
solo se documenta su estado (varios ya tenian trabajo de modelo hecho en
sesiones previas, pero sin cuota fiable no compiten en el ranking — quedan
en `/predictions/model-only`, consistente con el criterio "nunca inventar
`market_probability`").

## 6. Que queda pendiente (para retomar sin releer todo)

Esta sesion completo Fase 1 (esta auditoria) y una parte acotada pero real
de Fase 2 (root cause + fix de consistencia, `calibrated_probability` +
`signal_score` persistidos, formula de `signal_score` extendida y
documentada). **No** se llego a completar Fase 3-8 por el volumen del
encargo frente al tiempo disponible en esta sesion. Se prioriza dejar el
repo verde y documentado en vez de dejar cambios a medias sin terminar.

Pendiente, en orden sugerido:

1. **Fase 3 — Mejor prediccion por partido / mejor senhal del dia**:
   - Selector: para cada `match_id`, elegir la `Prediction` con mayor
     `signal_score` entre las que pasan `evaluate_quality_gate` (ya hay
     `rank_signals` para el ranking global; falta agrupar por partido).
   - Endpoint nuevo o extension de `/predictions/best` para exponer
     "mejor prediccion" por partido en listados de proximos partidos.
   - Requisitos minimos de la seccion 16 (mercado real, cuota valida,
     bookmakers suficientes, mercado reciente, modelo calibrado, edge
     minimo, sin contradiccion) — el filtro duro actual ya cubre casi
     todos salvo "sin contradiccion", que ahora es trivial de anhadir
     gracias al fix de la seccion 2: comprobar que ninguna otra prediccion
     del mismo grupo mutuamente excluyente para el mismo partido tiene
     `model_probability` tambien > 0.5 (deberia ser imposible tras el fix,
     pero es la comprobacion de seguridad barata de anhadir).
   - Etiquetas HIGH_PROBABILITY vs BEST_VALUE (seccion 3/18): con
     `signal_score` ya separado de probabilidad pura, es una funcion de
     comparacion directa, sin nuevo calculo.

2. **Fase 4 — Historico/settlement completo**:
   - Rellenar `actual_result` (columna ya anhadida, sin cablear) al
     liquidar en `evaluation_service.py` (o un nuevo `settlement_service.py`
     dedicado), junto con `outcome`/`settled_at` que ya se escriben.
   - Endpoints `/fixtures/finished`, `/history`, `/history/{id}` (o
     adaptando los nombres a los ya existentes) filtrando por
     `Match.status == "finished"`, nunca por fecha.
   - Frontend: pestaña Historico.

3. **Fase 5 — Metricas/calibracion sobre datos reales**: el modulo ya
   existe (`backtesting/metrics.py`, `backtesting/calibration.py`,
   `evaluation_service.py`) — falta el endpoint `/model/performance` que
   lo exponga segmentado (mercado/liga/rango de cuota/rango de
   probabilidad/rango de edge/version de modelo) y el dashboard frontend.
   Con esto en marcha, conectar `market_track_record` (seccion 4) deja de
   ser un parametro sin usar.

4. **Fase 6 — Frontend/UX**: navegacion Inicio/Proximos/Señales/Historico/
   Modelo, badges de anomalias (CONTRADICCION -- ya casi imposible tras el
   fix, pero conviene un test de regresion visible en UI; SEÑAL DE BAJA
   FIABILIDAD; OUTLIER; HIGH PROBABILITY/LOW VALUE), actualizar
   `frontend/types/index.ts` + `frontend/lib/api.ts` para los campos nuevos
   (`calibrated_probability`, `signal_score`) en las respuestas donde se
   expongan.

5. **Fase 7 — Los tests de la seccion 27 no cubiertos todavia**: de los 16,
   esta sesion anhadio cobertura directa para "no se seleccionan
   simultaneamente Over y Under como señales fuertes" y "probabilidades
   mutuamente excluyentes son coherentes" (`test_market_consistency.py`) y
   para el scoring de "mejor prediccion"/"high probability != high value"
   parcialmente (`test_ranking.py`, ya existia antes). Faltan: partido
   finalizado desaparece de Home / aparece en Historico, resultado se
   guarda, prediccion historica inmutable end-to-end, prediccion
   acertada/fallada correctamente determinada via el pipeline completo,
   MODEL_ONLY no entra en señales (probablemente ya cubierto
   indirectamente por el filtro duro, pero sin test explicito nombrado
   asi), partidos futuros no aparecen en jornada actual (related a
   `round_service.py`, ya testeado parcialmente), snapshots de cuotas
   funcionan, model_version queda guardada (ya se guarda, falta test
   explicito).

6. **Refactor arquitectonico completo del punto 2** (clasificador
   multinomial conjunto en vez de N binarios independientes): la solucion
   "de raiz" definitiva, no urgente porque el fix de renormalizacion ya
   corrige la inconsistencia observable, pero documentada aqui para no
   perderla de vista.

7. **CompetitionConfig centralizado** (seccion 14/15 del brief): hoy
   `Competition` (`db/models/core.py`) no tiene campos `priority`/`enabled`/
   tier CORE-vs-EXPERIMENTAL. No se toco esta sesion por acotar el alcance
   al problema de consistencia de mercados, que era el pedido explicito
   mas critico. Anhadir esos campos es un cambio de esquema pequenho
   (mismo patron `_sync_missing_columns`) cuando se aborde Fase 6/priorizacion
   de ligas.

## 7. Estado de los tests al cierre de esta sesion

`cd backend && pytest -q` (o `make test` desde la raiz): **160 passed**
(152 preexistentes + 8 nuevos: 6 en `test_market_consistency.py`, 2 en
`test_ranking.py`), 0 failed, solo warnings de deprecacion de
`datetime.utcnow()` preexistentes (no introducidos por este cambio).

No se toco nada de `frontend/` en esta sesion (ningun endpoint/contrato
existente cambio de forma), asi que no se ejecuto `npm run build` — no
deberia haber ningun impacto, pero queda pendiente confirmarlo en la
proxima sesion antes de anhadir los endpoints nuevos de Fase 3/4 que si
tocaran el contrato.

## 8. Fase 3-8 (sesion siguiente, 2026-09-20): que se completo

Esta sesion retomo el punto exacto donde quedo la seccion 6 y completo
Fase 3 a Fase 7 en el orden sugerido, mas esta Fase 8 (cierre). Resumen
por fase, con puntero exacto de codigo para cada pieza:

**Fase 3 — mejor prediccion / mejor senhal del dia / anomalias**
(`backend/app/prediction/anomaly.py`, nuevo modulo):
- `compute_anomaly_flags(prediction, match_predictions, settings)`:
  CONTRADICCION (misma logica de grupo mutuamente excluyente que
  `market_labels.MUTUALLY_EXCLUSIVE_GROUPS`, red de seguridad ahora que
  el fix de renormalizacion deberia hacerla casi imposible),
  SENAL_BAJA_FIABILIDAD (`confidence`/`data_quality` < 0.4),
  OUTLIER (`market_quality_tier` == LOW), HIGH_PROBABILITY_LOW_VALUE
  (`model_probability >= 0.75` y `edge < 0.03`). Umbrales documentados en
  el docstring del modulo, no repartidos por el codigo.
- `best_prediction_per_match(predictions_by_match)`: usa `rank_signals`
  (filtro duro) + excluye CONTRADICCION, nunca elige por probabilidad/
  edge/cuota en crudo.
- `/predictions/top-signals` y `/predictions/best`
  (`backend/app/api/routes/predictions.py`) excluyen ahora cualquier
  prediccion con CONTRADICCION (nunca Over y Under como señales fuertes a
  la vez, seccion 2).
- Nuevo `/predictions/best-of-day`: filtro MAS estricto (edge minimo =
  2x el umbral general, calibracion disponible, sin contradiccion), con
  cada requisito trazable en `explanation`; devuelve `prediction: null`
  cuando nadie lo cumple (nunca se rebaja el filtro para forzar un
  resultado).
- `/matches/{id}/predictions` expone `anomaly_flags`/`is_best_prediction`
  por prediccion (`backend/app/api/routes/matches.py`).

**Fase 4 — settlement / Historico**
(`backend/app/services/evaluation_service.py::settle_finished_predictions`):
- Crea `PredictionResult` (tabla YA EXISTENTE desde Fase 2, nunca usada
  hasta ahora) para cada `Prediction` de un partido `status="finished"`
  sin resultado todavia. Idempotente (no duplica si se llama dos veces).
  **Nunca** toca los campos predictivos de `Prediction` -- solo INSERTA
  una fila en la tabla separada `prediction_results` (relacion 1-a-1).
  `actual_result` es el marcador literal (`"2-1"`), no una interpretacion
  por mercado (mas simple y siempre derivable, sin inventar nada).
- Cableado en el comando `evaluate` del CLI (`backend/app/cli.py`), que ya
  estaba dentro de `refresh` -- ningun comando nuevo que acordarse de
  ejecutar.
- Endpoints nuevos: `/matches/fixtures/finished` (filtra por
  `Match.status`, nunca por fecha), `/history`, `/history/{match_id}`
  (`backend/app/api/routes/history.py`).
- Frontend: `frontend/app/history/page.tsx` (listado con mejor prediccion
  + ACERTADA/FALLADA/sin resolver) y `frontend/app/history/[id]/page.tsx`
  (detalle: TODAS las predicciones del partido vs la realidad).

**Fase 5 — metricas/calibracion reales**
(`backend/app/services/evaluation_service.py::real_performance_report`):
- Reusa integramente `backtesting/metrics.py`
  (`model_quality_metrics`, `performance_by_probability_bucket`,
  `performance_by_edge_bucket`, `market_strategy_metrics`), pero
  aplicado SOLO sobre predicciones con `PredictionResult` (liquidadas),
  segmentado por competicion/mercado/version de modelo.
- Endpoint `GET /models/performance` (`backend/app/api/routes/models.py`,
  declarado ANTES de `/models/{model_id}` a proposito para no colisionar
  con la ruta de id entero).
- Frontend: `frontend/app/model/page.tsx` -- curva de calibracion
  predicho-vs-real por bucket, resultado real por rango de edge, ROI
  hipotetico, sin libreria de graficos nueva (tablas + barras CSS con la
  misma paleta del resto del tablero).
- `market_track_record` (parametro de `signal_score`, Fase 2) **sigue sin
  cablearse automaticamente**: ahora que `/models/performance` existe,
  conectar "1 - error de calibracion historico del mercado" es una
  llamada mas, no un rediseño -- se dejo fuera de esta sesion por acotar
  alcance (habria significado recalcular y cachear ese numero en el
  pipeline de generacion de predicciones, un cambio con mas superficie
  que las fases pedidas explicitamente).

**Fase 6 — frontend/UX**:
- Nav Inicio/Señales/Histórico/Modelo (`frontend/app/layout.tsx`).
- `frontend/components/BestOfDayWidget.tsx` en Inicio (usa
  `/predictions/best-of-day`, muestra honestamente "ninguna" cuando no
  hay candidata).
- `frontend/components/AnomalyBadge.tsx`: badges de las 4 anomalias de
  Fase 3, integrados en `PredictionRow.tsx` junto a la estrella ★ de
  "mejor prediccion del partido".
- Colores solo transmiten estado (ambar=aviso, verde/rojo=acertada/
  fallada real, nunca "todo verde" por edge positivo).
- Auditoria de copy: no se encontro (ni se anhadio) "segura"/"ganadora"/
  "100% segura" en ningun texto del frontend (`grep -rniE` limpio antes
  y despues del cambio).
- **No hecho** (fuera del alcance pedido, no roto): rediseño visual
  completo de las cards existentes (`MatchCard`/`PredictionRow` ya tenian
  jerarquia razonable de una sesion anterior); esta sesion extendio esos
  componentes en vez de rehacerlos, para no arriesgar regresiones
  visuales sin pedido explicito de rediseño total.

**Fase 7 — tests**: +19 tests nuevos en total sobre los 160 de cierre de
Fase 2 (`test_settlement.py`: 9; `test_signal_exclusions.py`: 2; el resto
de items de la seccion 27 del brief -- jornada actual vs futura, snapshots
de cuotas -- ya tenian cobertura preexistente en `test_round_service.py` /
`test_data_service_odds_preservation.py`, confirmado leyendo esos ficheros
en vez de duplicar tests). **171 passed, 0 failed** al cierre de esta
sesion.

**No leakage / inmutabilidad (verificado, no solo documentado)**:
`test_settlement_never_mutates_predictive_fields_of_prediction` congela
`model_probability`/`edge`/`signal_score`/`market_odds`/`model_version_id`
ANTES de liquidar y comprueba que son bit-a-bit iguales despues -- el
mecanismo de inmutabilidad no es solo el diseño de esquema (tabla
`prediction_results` separada, 1-a-1, sin UPDATE posible sobre
`Prediction`), esta comprobado en un test real end-to-end usando el
pipeline de entrenamiento/prediccion real, no un mock.

**Fase 8 — build final**: `cd backend && pytest -q` → **171 passed**, 0
failed (solo warnings preexistentes de `datetime.utcnow()` y de
`FutureWarning`/`UserWarning` de pandas/sklearn, ninguno introducido por
este cambio). `cd frontend && npm run build` → compila limpio, 8 rutas
(`/`, `/history`, `/history/[id]`, `/matches/[id]`, `/model`,
`/signals/[id]`, `/top-signals`, `/_not-found`).

## 9. Que queda deferido (para una sesion futura)

En el mismo orden de prioridad que dejaria el equipo si se retomara hoy:

1. **`market_track_record` sin cablear automaticamente** (ver seccion 5
   de este documento): el endpoint `/models/performance` ya da todo lo
   necesario (Brier/calibracion real por mercado); falta que
   `services/prediction_service.py::generate_predictions_for_competition`
   consulte ese historico al generar cada prediccion nueva y se lo pase a
   `signal_score`. Requiere decidir una ventana temporal razonable (¿ultimos
   N partidos liquidados de ese mercado? ¿esa competicion o todas?) antes
   de implementarlo -- no es solo mecanica, es una decision de producto
   pequenha pero real.
2. **Refactor arquitectonico del clasificador multinomial conjunto**
   (ver seccion 6, punto 6, sin cambios: sigue sin ser urgente).
3. **`CompetitionConfig` centralizado / tiers CORE-vs-EXPERIMENTAL** (ver
   seccion 6, punto 7, sin cambios: sigue sin abordarse).
4. **Corners/Cards/Asian Handicap/Draw No Bet siguen FUTURE-tier**, sin
   cambios respecto a la tabla de la seccion 5 (decision de producto de
   2026-09-18, no revisada ni cuestionada esta sesion: ninguna de esas
   fuentes de cuota nuevas se ha vuelto viable, no se ha buscado una
   razon para anhadirlas).
5. **Rediseño visual completo del frontend** (seccion 25 del brief, mas
   alla de lo ya hecho en Fase 6): esta sesion prioriza extender
   componentes existentes (badges, widget nuevo, paginas nuevas) sobre
   rehacer la jerarquia visual de cero, que no se pidio explicitamente y
   habria sido mas riesgo que valor dado el tiempo disponible.
6. **Segmentacion adicional de `/models/performance`** por rango de cuota
   explicito (hoy segmenta por competicion/mercado/version de modelo, y
   cada segmento YA incluye desglose por bucket de probabilidad/edge --
   falta un filtro de query explicito `odds_min`/`odds_max` si se quiere
   acotar el propio segmento por rango de cuota, no solo ver el
   desglose).
