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

## 10. Fase 9 (sesion 2026-11): "motor profesional de prediccion"

El usuario pidio explicitamente "el modelo mas preciso que haya en el
mundo" y dio permiso para decidir e implementar todo sin pedir
confirmacion por fase, tras una auditoria Fase 1 propia de esta sesion
(no la de las secciones 1-9 de arriba, que son de la sesion anterior).
Esa auditoria Fase 1 identifico gaps reales frente a un brief mucho mas
ambicioso (CatBoost/LightGBM, Poisson bivariado, MLflow, SHAP, Shin's
method, deteccion de drift, CLV, cuantificacion de incertidumbre) y
propuso un orden de implementacion por impacto/esfuerzo. Esta sesion
ejecuto esa lista completa, con la misma disciplina que las sesiones
anteriores: nunca sustituir un componente que funciona sin backtest real,
tests en verde en cada commit, nunca inventar un numero sin datos
suficientes.

**1. Clasificador multinomial conjunto por grupo** (la correccion de RAIZ
que la seccion 6 punto 6 de este documento dejaba pendiente):
`models/goals/multinomial_classifier.py::MultinomialGroupClassifier` -- un
unico modelo por grupo mutuamente excluyente (home_win/draw/away_win,
over_2_5/under_2_5) en vez de N binarios independientes, con probabilidades
que suman 1 por construccion. **Nunca sustituye al clasificador
independiente a ciegas**: `models/goals/model_selection.py` compara ambos
(mas LightGBM, ver punto 2) por log loss real en el holdout, GRUPO A GRUPO
(nunca mercado a mercado dentro del mismo grupo -- un bug real detectado
por un test propio de esta sesion antes de llegar a produccion: elegir
ganadores distintos para "home_win" y "draw" reintroduce la misma
inconsistencia que se queria corregir). La decision queda trazada en
`ModelVersion.metrics["markets"][mercado]["ml_winner"]`.

**2. LightGBM como tercer candidato** (`models/goals/gradient_boosting.py`):
se entrena SIEMPRE junto a los otros dos y compite por el mismo backtest.
Hiperparametros deliberadamente conservadores tras comprobar
EMPIRICAMENTE que una configuracion mas agresiva sobreajusta en datasets
de unos cientos de partidos (peor que adivinar al 50% en el holdout) --
documentado en el modulo, no solo en el commit. Explicabilidad real via
SHAP (`prediction/shap_explanation.py`) para cuando este candidato gana,
ya que no expone coeficientes lineales.

**3. Ensemble generalizado de 2 a N modelos**
(`models/ensemble/ensemble.py::learn_ensemble_weights`): grid search sobre
pesos no negativos que suman 1 para cualquier numero de candidatos, no
solo 2 -- paso previo necesario para los puntos 1 y 2.

**4. `market_track_record` conectado por fin a `signal_score`**
(seccion 4 de este documento dejaba el parametro sin cablear):
`services/evaluation_service.py::market_track_records()` calcula "1 -
error de calibracion historico" por (competicion, mercado) sobre
predicciones YA LIQUIDADAS, con un minimo de 30 liquidaciones antes de
confiar en el numero. Cableado en los 3 endpoints de ranking real y en el
`signal_score` CONGELADO al generar la prediccion.

**5. Cuantificacion de incertidumbre** (`prediction/uncertainty.py`):
intervalo de Wilson sobre `model_probability` usando el tamanho de
muestra efectivo (partidos previos conocidos por equipo), ensanchado por
el desacuerdo entre submodelos. `Prediction.uncertainty_low/high`
persistidos y mostrados en el frontend junto a la probabilidad.

**6. Deteccion de drift** (`monitoring/drift.py`): Population Stability
Index (metrica estandar de scoring de riesgo crediticio, umbrales
0.10/0.25 ampliamente usados) comparando los ultimos N partidos jugados
contra el resto del historico, por feature. Comando CLI
`football-edge detect-drift`, puramente informativo, nunca bloquea
refresh/train.

**7. Shin's method: investigado, NO implementado.** Se intento verificar
la formula cerrada (parametro z, expresion con raiz cuadrada) contra una
fuente autoritativa antes de escribir codigo -- el entorno de esta sesion
no tenia acceso de red a ninguna fuente que lo confirmara (arxiv.org,
karlwhelan.com, en.wikipedia.org, todas bloqueadas por la politica de red
del entorno). Implementar una formula de probabilidad sin poder
verificarla arriesgaba corromper `market_probability`/edge en silencio --
peor que no implementarla. Documentado en `market/vig.py` con esta
justificacion explicita; sigue en pie la normalizacion proporcional.

**8. Infraestructura de CLV (Closing Line Value)**: tabla nueva
APPEND-ONLY `MatchOddsHistory` (`db/models/matches.py`), escrita en
paralelo a `MatchOdds` (que sigue borrando-y-reemplazando el snapshot
vigente) -- limite honesto, solo existe desde este cambio, CLV nunca
puede calcularse retroactivamente. `market/clv.py` + cableado en
`settle_finished_predictions`. Bug real encontrado escribiendo los tests
(solo reproducible con la suite completa, nunca en aislamiento): `Match`
no tenia cascade hacia `MatchOddsHistory`, asi que borrar un partido
(`reset_scheduled`) dejaba historico huerfano que SQLite podia "adoptar"
en un partido nuevo que reciclara el mismo id -- corregido con el mismo
cascade que `odds`/`statistics` ya tenian.

**9. CompetitionConfig** (`tier`/`enabled`/`priority` en `Competition`,
seccion 14/15 de este documento): nullable a proposito, porque
`_sync_missing_columns` anhade columnas sin DEFAULT a nivel de BD y una
columna NOT NULL habria dejado las competiciones ya existentes con NULL,
invisibles ante un filtro `WHERE enabled = true`.

**10. MLflow** (`services/mlflow_tracking.py`): desactivado por defecto,
nunca propaga un fallo hacia el entrenamiento real. Backend SQLite local
por defecto, no file store plano -- MLflow >= 3 pone ese backend en
maintenance mode y lo rechaza salvo que se fuerce explicitamente,
descubierto escribiendo el test de este modulo.

**Disciplina mantenida en los 10 puntos**: cada uno se commiteo por
separado con la suite completa en verde antes del siguiente (de 203 tests
al empezar esta fase a 256 al cerrarla), lint sin anhadir deuda nueva
sobre la ya existente, y `npm run build` del frontend verificado despues
de cada cambio que tocaba la API. Tres bugs reales encontrados y
corregidos ANTES de llegar a produccion, todos por tests escritos en esta
misma sesion (no reportados por el usuario): la seleccion de modelo ML
mercado-a-mercado dentro de un grupo (punto 1), el cascade de borrado
faltante para `MatchOddsHistory` (punto 8), y el file store de MLflow
deprecado (punto 10) -- los tres documentados en sus modulos
correspondientes, no solo aqui.

**Honestamente, no es "el modelo mas preciso del mundo"** -- esa frase no
tiene una definicion verificable. Lo que si es cierto: cada mejora de esta
fase se valida contra un backtest real antes de usarse en produccion
(nunca una sustitucion a ciegas), y donde no se pudo verificar algo con
confianza suficiente (Shin's method) se dejo documentado en vez de
adivinado. Pendiente para una sesion futura, en el mismo orden de
prioridad que se dejaria hoy:

1. Conectar CLV/incertidumbre a `signal_score`/`market_quality` (hoy son
   informativos, mostrados pero no usados para rankear).
2. Backtest real comparando el clasificador multinomial/LightGBM contra
   el baseline anterior con datos de produccion reales (esta sesion solo
   verifico con datos sinteticos y el holdout de entrenamiento normal).
3. Revisar Shin's method si en el futuro hay acceso de red a una fuente
   verificable, o si el usuario pega el texto exacto del paper.
4. Extender `GradientBoostingMarketModel` a una variante conjunta por
   grupo (como `MultinomialGroupClassifier` pero con arboles) si el
   binario independiente demuestra ser el cuello de botella.

## 11. Fase 10 (sesion 2026-11, continuacion): "motor profesional 2.0" — revision del ranking

El usuario reporto el problema real de raiz de todo este bloque, con un
caso concreto (Getafe vs Atletico Madrid, doble oportunidad 1X: modelo
81.9% frente a mercado 41.9%, edge +40pp) y pidio una revision completa
del motor de ranking/edge/EV, auditando primero, implementando y
validando despues, sin pedir confirmacion fase a fase.

### 11.1 Problemas encontrados (auditoria con codigo real, no supuestos)

1. **`signal_score` (produccion) usa `model_probability` CRUDO, nunca
   `calibrated_probability`** (`prediction/ranking.py`) — documentado ya
   como decision diferida, pero nunca resuelta: una probabilidad extrema
   sin calibrar domina el ranking via `model_probability**2`.
2. **`signal_score` NUNCA usa el intervalo de incertidumbre**
   (`uncertainty_low`/`uncertainty_high`, ya calculado y persistido desde
   la Fase 9) — una predicción con intervalo [0.40, 0.95] puntuaba
   EXACTAMENTE igual que otra con [0.78, 0.85] al mismo punto estimado.
   Esta es la causa mecanica mas directa del caso reportado.
3. **El EV (`compute_expected_value`) tambien usa `model_probability`
   crudo**, nunca una version conservadora — contradice la propia
   formula de EV que pide rigor estadistico.
4. **La calibracion de produccion SI existe y SI se ajusta sobre el
   ENSEMBLE real** (`model_service.py`, `IsotonicCalibrator` sobre la
   probabilidad del ensemble ya renormalizada, en un holdout real) —
   correccion a una suposicion propia de esta misma sesion horas antes.
   Pero: (a) usa un UNICO split train/holdout, nunca walk-forward: mucho
   menos riguroso que el motor de backtest que existe para otras cosas;
   (b) solo usa isotonic — `PlattCalibrator` existe en el codigo
   (`models/calibration/calibrators.py`) desde la Fase 9 pero nunca se
   habia usado en el entrenamiento real; (c) el comando
   `calibration-report` de la CLI (el unico que se habia corrido contra
   datos reales hasta ahora) NO evalua esto: evalua calibracion sobre
   `DixonColesModel` AISLADO via walk-forward
   (`backtesting/engine.py::run_walk_forward_backtest`, que solo sabe
   entrenar un `GoalsModel` suelto, nunca el ensemble con el clasificador
   ML) — la herramienta de validacion rigurosa probaba el modelo
   EQUIVOCADO. Nunca se habia podido responder con confianza si calibrar
   el ensemble real (con Platt o con isotonic) mejora algo.
5. **Vig removal**: solo normalizacion proporcional (`market/vig.py`) —
   Shin's method sigue deliberadamente sin implementar (ver seccion 10,
   punto 7), sin cambios en esta fase.
6. **`market_track_record` SI esta conectado de verdad**
   (`evaluation_service.py::market_track_records`, 1 − ECE historico real
   por competicion/mercado, >= 30 liquidaciones) — otra correccion a una
   suposicion propia: esto ya funcionaba bien desde la Fase 9.
7. **No existia ningun motor de backtest que supiera evaluar el ENSEMBLE
   completo (DC + ML + pesos aprendidos) fold a fold** — solo para un
   `GoalsModel` aislado. Sin esto, era imposible validar sin leakage
   ninguna mejora de calibracion/ranking contra lo que realmente se sirve
   en produccion.

### 11.2 Que se ha cambiado

- **`backtesting/ensemble_engine.py`** (nuevo): walk-forward real del
  ENSEMBLE completo (DC + 3 candidatos ML + seleccion de ganador por
  grupo + pesos aprendidos + calibradores isotonic/platt), con un holdout
  de seleccion/calibracion separado del fold de test en cada iteracion —
  nunca leakage. Comando CLI `ensemble-calibration-report`.
- **`Prediction.risk_adjusted_expected_value`**: EV calculado con
  `uncertainty_low` (extremo conservador del intervalo) en vez de
  `model_probability` — nunca mas optimista que `expected_value`.
  (`prediction/predictor.py`, `services/prediction_service.py`, API,
  frontend).
- **`prediction/anomaly.py::HIGH_UNCERTAINTY` ("ALTA_INCERTIDUMBRE")**:
  nueva flag que SI excluye de "mejor prediccion"/"señal fuerte"
  (`is_strong_signal`), igual que `CONTRADICCION` — antes solo habia
  flags informativas. Umbral (ancho de intervalo >= 0.60) derivado
  EMPIRICAMENTE de la distribucion real de anchos en un backtest
  sintetico (mediana ~0.32, p90 ~0.59) — un primer intento con 0.30
  resulto demasiado agresivo (regresion real: 12 tests rompieron, todos
  corregidos antes de continuar).
- **`prediction/ranking.py::signal_score_v2`/`rank_signals_v2`** (nuevo,
  NUNCA sustituye a v1 en produccion por si solo): usa
  `model_probability` (cruda, igual que v1 -- ver 11.5: la calibrada se
  descarto con evidencia real), `risk_adjusted_expected_value` en vez de
  `edge` bruto, y un factor CONTINUO de penalizacion por ancho de
  intervalo (ademas del corte binario de `HIGH_UNCERTAINTY`).
- **`backtesting/ranking_comparison.py`** (nuevo): compara v1 vs v2 sobre
  predicciones YA LIQUIDADAS, por TOP-N de cada estrategia (ROI, win
  rate, PnL, drawdown, Brier, log loss). Sin riesgo de leakage: ambos
  scores usan solo campos ya congelados antes del resultado real.
  Comando CLI `compare-ranking-strategies`.
- **Dashboard**: `AnomalyBadges` cableado en `MatchCard`/`top-signals`
  (antes solo en detalle de partido/historico); filtro nuevo "EV minimo"
  en `/predictions/top-signals` (`min_ev`, fraccion, filtro SQL sobre
  `Prediction.expected_value`).

### 11.3 Metricas usadas ahora (todas ya existian o se añaden aqui, ninguna inventada)

Brier score, log loss, Expected Calibration Error (`models/calibration/metrics.py`,
ya existian), ROI/yield/win-rate/max-drawdown/PnL
(`backtesting/metrics.py`, `ranking_comparison.py`, ya existian para
backtest sintetico, reusadas aqui sobre liquidaciones reales), e
intervalo de Wilson para incertidumbre (`prediction/uncertainty.py`, Fase
9). Reliability diagrams: `reliability_curve` ya existe en
`models/calibration/metrics.py` desde antes de esta fase.

### 11.4 Como funciona el ranking nuevo (signal_score_v2)

```
score_v2 = model_probability^2 * max(risk_adjusted_expected_value, 0) * confidence
           * data_quality * market_quality_multiplier * market_track_record
           * factor_incertidumbre(ancho_intervalo)
```

(Nunca `calibrated_probability`: el diseno inicial de esta formula la
preferia cuando existia, pero `ensemble-calibration-report` corrido
contra LaLiga -- ver 11.5 -- mostro que isotonic y Platt empeoran
Brier/log-loss/ECE en los 4 mercados centrales probados. Corregido con
esa evidencia, no a priori.)

`factor_incertidumbre` decae linealmente de 1.0 (ancho=0) a un suelo de
0.30 (ancho=1.0, el maximo teorico) — nunca colapsa a cero por si solo
(eso ya lo hace la exclusion binaria `HIGH_UNCERTAINTY` aparte). Un EV
ajustado al riesgo negativo anula el score entero (igual que v1 anula con
edge negativo). **v2 NO esta activo en produccion**: solo
`rank_signals`/`signal_score` (v1) generan y congelan lo que ve el
usuario hoy. v2 es la candidata que se backtestea contra v1.

### 11.5 Resultados comparativos del backtesting (datos REALES, corridos por el usuario)

Esta sesion no tiene acceso al historico real (vive solo en el Docker
Compose del usuario), pero el usuario corrio los comandos y pego la
salida real -- estos son resultados REALES, no sinteticos.

**`ensemble-calibration-report` (laliga, los 4 mercados centrales del
caso que motivo esta revision), walk-forward real, 7 folds, n=2719:**

| mercado           | raw ECE | isotonic ECE | platt ECE |
|--------------------|---------|--------------|-----------|
| home_win           | 0.0400  | 0.0577 (peor)| 0.0445 (peor) |
| draw               | 0.0243  | 0.0433 (peor)| 0.0279 (peor) |
| away_win           | 0.0403  | 0.0526 (peor)| 0.0321 (peor) |
| double_chance_1x   | 0.0592  | 0.0923 (peor)| 0.0724 (peor) |

Brier score y log loss muestran el mismo patron en los 4 mercados: raw
siempre mejor que isotonic y que Platt. **Conclusion real, no supuesta**:
ni isotonic ni Platt mejoran el ensemble real en NINGUNO de los 4
mercados centrales -- consistente, no ruido de un mercado aislado. Como
consecuencia directa de esta evidencia, `signal_score_v2` se corrigio
para NUNCA usar `calibrated_probability` (el diseno inicial de este
mismo documento, seccion 11.4, lo preferia cuando existia -- era
incorrecto, corregido aqui con los numeros reales en la mano, no a
priori).

**`compare-ranking-strategies` (todas las competiciones/mercados,
n=1133 predicciones liquidadas con cuota real):**

```
       top-5        top-10       top-20       top-50       top-100      all (n=1133)
v1   roi=+0.007   roi=-0.219   roi=-0.138   roi=+0.062   roi=+0.016   roi=-0.083
     win=60.0%    win=50.0%    win=55.0%    win=72.0%    win=66.0%    win=51.5%
v2   roi=+0.232   roi=+0.018   roi=-0.032   roi=-0.019   roi=+0.013   roi=-0.083
     win=80.0%    win=60.0%    win=65.0%    win=58.0%    win=54.0%    win=51.5%
```

(`all` es identico entre v1/v2 por construccion: el conjunto de
apuestas no cambia, solo el orden -- lo que SI varia es el max_drawdown,
ligeramente mejor en v2.)

**Lectura honesta**: a n=1133 (la cifra mas fiable que hay), apostar a
TODO lo que pasa el filtro duro pierde dinero (ROI -8.3%) en ambas
estrategias -- elegir bien importa, confirmado con datos reales. En los
tramos TOP-N (donde se juega la diferencia real entre v1 y v2), v1
muestra un patron NO monotono y a contramano: su top-10/20 (sus señales
"mas seguras") rinden PEOR que su propio top-50 -- exactamente el
sintoma que el usuario reporto (el ranking actual no correlaciona bien
con lo que realmente acierta). v2 top-5 rinde notablemente mejor
(ROI+23.2%, 80% de acierto) que v1 top-5. n=5/10/20 son muestras
demasiado pequenas para una conclusion estadisticamente solida por si
solas, pero la direccion es consistente con el diagnostico del usuario
Y el usuario confirmo via la app en vivo que el sintoma seguia
presente (porque v1 seguia siendo el default en ese momento) -- con
esa confirmacion real adicional, se decidio ACTIVAR v2 en produccion
(ver abajo), no seguir esperando mas muestra de forma indefinida.

**ACTIVADO EN PRODUCCION (2026-11)**: `signal_score_v2` sustituye a
`signal_score` (v1) en TODOS los puntos que generan lo que ve el
usuario: `services/prediction_service.py` (snapshot congelado de cada
prediccion nueva), `/predictions/top-signals`, `/predictions/best`,
`/predictions/best-of-day`, `/predictions/summary`,
`/predictions/best/debug`, y `prediction/anomaly.py::best_prediction_per_match`
(la "mejor prediccion" de cada partido). `signal_score`/`rank_signals`
(v1) NUNCA se borraron -- siguen existiendo como funciones, y
`backtesting/ranking_comparison.py` las RECALCULA explicitamente
(nunca lee `prediction.signal_score`, que desde ahora congela v2) para
poder seguir comparando v1 contra v2 -- o v2 contra una v3 futura --
indefinidamente, independientemente de cual sea el default activo.

### 11.6 Limitaciones que siguen existiendo

- Shin's method sigue sin implementar (sin fuente verificable, ver
  seccion 10 punto 7) — solo normalizacion proporcional.
- La decision de activar v2 se tomo con evidencia real direccionalmente
  fuerte pero estadisticamente delgada en el tramo top-N (n=5-20) —
  nunca se fingio que fuera una prueba estadistica solida por si sola.
  Se recomienda seguir corriendo `compare-ranking-strategies`
  periodicamente a medida que se liquiden mas predicciones, para
  confirmar que v2 sigue rindiendo bien con mas muestra.
- El umbral de `HIGH_UNCERTAINTY` (0.60) y la pendiente/suelo de
  `factor_incertidumbre` en v2 (0.70/0.30) se derivaron de un dataset
  SINTETICO (Poisson simple) por falta de acceso a datos reales desde
  este entorno — deberian remedirse contra predicciones reales ya
  liquidadas una vez haya suficiente historico, no asumirse validos para
  siempre.
- `ensemble_calibration_report`/`compare_ranking_strategies` son
  herramientas de diagnostico nuevas, verificadas solo con datos
  sinteticos en esta sesion (10 + 6 tests respectivamente) — su
  comportamiento con el volumen y la distribucion reales de las 5 ligas
  del MVP todavia no se ha observado.
- El "indicador de fiabilidad" pedido explicitamente ya existe repartido
  en varios campos (`signal_tier`, `market_quality`, `anomaly_flags`,
  `confidence`, `data_quality`) en vez de un unico numero compuesto
  nuevo — evita inventar una metrica adicional no validada, pero exige
  mirar mas de un campo para "fiabilidad" en vez de uno solo.

## 12. Fase 11 (sesion 2026-11, continuacion): factor de fiabilidad del edge

### 12.1 Problema real encontrado tras activar v2

El usuario reporto, tras regenerar predicciones con `signal_score_v2` ya
activo en produccion: "sigo viendo lo mismo eh". Se diagnostico en vivo
contra la BD real: el caso original que motivo toda la revision
(match_id=61, Getafe-Atletico, mercado `double_chance_1x`, edge +39.6pp)
seguia siendo la #1 del ranking.

Causa raiz real (no supuesta): `uncertainty_low`/`uncertainty_high` de
ese partido eran **0.718/0.911** — un intervalo de Wilson ESTRECHO
(ancho 0.193, muy por debajo del umbral de `HIGH_UNCERTAINTY`, 0.60),
porque el modelo tiene mucha muestra historica para ese tipo de
prediccion. `factor_incertidumbre` (seccion 11.4) mide ruido de
MUESTRA — "cuanto puede fiarse el modelo de su propia estimacion dado
el tamanho de la muestra" — nunca "cuanto discrepa esa estimacion de una
fuente independiente fuerte (el mercado)". Son dos tipos de
incertidumbre DISTINTOS, y el sistema solo tenia el primero. Por eso el
EV ajustado al riesgo de ese partido seguia siendo alto
(`risk_adjusted_expected_value=0.700`) y el caso seguia dominando el
ranking: v2 corrigio el sintoma sintetico que se habia probado, pero no
el mecanismo real que lo producia.

### 12.2 Evidencia real antes de corregir (nunca un umbral inventado)

Antes de anhadir cualquier penalizacion por tamanho de edge, se pidio
evidencia real de que un edge mas grande REALMENTE acierta peor en este
sistema. Se construyo `scripts/debug_edge_vs_hitrate.py` (reutiliza
`backtesting/metrics.py::performance_by_edge_bucket`, ya existente,
ninguna metrica nueva) y el usuario lo corrio contra las 1133
predicciones liquidadas reales:

| edge (pp) | n   | hit rate | ROI    |
|-----------|-----|----------|--------|
| 0-5       | —   | 53.4%    | +3.2%  |
| 5-10      | —   | 41.4%    | negativo |
| 10-15     | —   | 31.8%    | -46.4% |

La caida es practicamente monotona, y se mantiene igual o se acentua
restringiendo a mercado bien respaldado (>=4 casas). Conclusion real:
en este sistema, un edge mas grande es señal de que el modelo
probablemente se equivoco (sesgo del ensemble frente a un mercado bien
informado), no de mejor oportunidad — exactamente la intuicion original
del usuario ("a veces la mejor [señal] es la que mas probabilidad dan
tanto el modelo y las casas y que se pague mejor").

### 12.3 Primer intento (INSUFICIENTE, descartado tras verificarlo en vivo)

Con esa evidencia (y solo con ella) se anhadio un primer mecanismo:

- `services/evaluation_service.py::fit_edge_reliability_curve(db)`: ajusta
  una `IsotonicRegression` (misma herramienta que
  `models/calibration/calibrators.py`, aqui con `increasing=False`
  porque la relacion real observada es DECRECIENTE) sobre
  `(edge_en_puntos_porcentuales -> resultado real)`, agregada sobre
  TODAS las competiciones/mercados a la vez (asi se midio la evidencia).
- Un factor `edge_reliability_factor(edge, curve)` que normalizaba el
  hit-rate empirico de ese tamanho de edge contra el de un edge minimo
  (~0pp) y se MULTIPLICABA al final de `signal_score_v2`.

**Esto se desplego y se verifico EN VIVO contra la BD real del
usuario (no se dio por valido sin comprobarlo)**: el factor SI se
aplicaba (redujo el score de Getafe de 0.2015 a 0.0561, un recorte real
del 72%), pero el caso **seguia siendo el #1** del ranking
(`docker compose exec backend python -c "..."` contra `rank_signals_v2`
sobre la jornada actual real, ver el historial de este chat). Causa:
un descuento multiplicativo APLICADO DESPUES no corrige la causa de
fondo -- `risk_adjusted_expected_value` (+0.70) seguia calculado con
`uncertainty_low=0.718`, un numero que la propia evidencia (hit rate
real ~14% a un edge de este tamanho) dice que es irrealmente optimista.
Descontar el score final de un EV ya inflado no basta: el EV mismo
tenia que corregirse. Esta seccion se deja escrita (en vez de borrarse)
porque documenta honestamente un intento real que no funcionaba
numericamente, no solo en teoria.

### 12.4 La correccion real: `reality_capped_expected_value`

En vez de un factor aparte, la misma curva se usa para poner un TECHO
al EV mismo, antes de que entre en la formula:

- **`prediction/ranking.py::reality_capped_expected_value(risk_adjusted_ev,
  edge, market_odds, curve)`** (funcion PURA, sustituye a
  `edge_reliability_factor`): calcula `real_hit_rate = curva.predict(edge*100)`
  y `real_ev = real_hit_rate * market_odds - 1`, y devuelve
  `min(risk_adjusted_ev, real_ev)`. Nunca un blend arbitrario (la
  seccion 9 del brief original lo pide explicitamente): es un TECHO,
  mismo principio que ya usa `uncertainty_low` como cota conservadora
  sobre `model_probability` -- aqui se anhade una segunda cota
  conservadora, independiente, sobre el sesgo SISTEMATICO del ensemble
  para ESTE TAMANO de edge (en vez de sobre el ruido de muestra de
  este partido en concreto), y se toma el minimo de las dos. Neutro
  (`risk_adjusted_ev` sin tocar) sin curva, `edge` o `market_odds`.
- `signal_score_v2` pasa el EV por este techo antes de recortarlo a 0:

```
ev_techo   = reality_capped_expected_value(risk_adjusted_expected_value, edge, market_odds, curva)
score_v2 = model_probability^2 * max(ev_techo, 0) * confidence
           * data_quality * market_quality_multiplier * market_track_record
           * factor_incertidumbre(ancho_intervalo)
```

- `fit_edge_reliability_curve(db)` (sin cambios) sigue calculando la
  curva; el parametro `edge_reliability_curve` de
  `signal_score_v2`/`rank_signals_v2`/`best_prediction_per_match` (sin
  cambios de firma ni de cableado en produccion -- solo cambio lo que
  pasa POR DENTRO con ese parametro) se sigue pasando igual en todos
  los puntos ya cableados en la seccion 12 original:
  `services/prediction_service.py`, los 5 endpoints de
  `/predictions/*`, `/matches/*`, `/history/*`, `agent/tools.py`,
  `agent/alerts.py`.

**Verificado con los numeros reales del caso Getafe**: edge=39.6pp,
cuota=2.37, `uncertainty_low`=0.718 (EV optimista = +0.70). La curva
real predice un hit rate de ~15% para ese tamanho de edge (clip al
extremo mas alto observado en la muestra real). `real_ev = 0.15*2.37-1
≈ -0.64`. `min(+0.70, -0.64) = -0.64` -> `max(-0.64, 0) = 0` -> el
score se anula por completo (en vez de solo reducirse un 72%). Un edge
modesto y bien respaldado por la curva (hit rate real cercano al
break-even de su propia cuota) no se ve afectado -- el techo solo actua
sobre el optimismo EXCESIVO de edges grandes, nunca sobre cualquier
edge por igual.

### 12.5 Tests anhadidos

- `test_ranking.py`: tests para `reality_capped_expected_value` (neutro
  sin curva/edge/cuota, recorta un EV optimista al valor real implicado
  por la curva para un edge tipo Getafe, nunca SUBE un EV ya peor que
  el techo, apenas toca un edge modesto bien respaldado);
  `signal_score_v2` con/sin curva (reproduccion explicita del caso
  Getafe real: antes de conectar la curva, el EV optimista seguia dando
  score positivo; con la curva, el score se anula por completo);
  `rank_signals_v2` y `best_prediction_per_match` pasando la curva de
  extremo a extremo.
- `test_evaluation_service.py`: 2 tests para `fit_edge_reliability_curve`
  (`None` por debajo del minimo; la curva ajustada predice menor hit
  rate para edge grande que para edge pequenho). Corridos contra un
  motor SQLite EN MEMORIA aislado (no el fichero compartido de toda la
  sesion de pytest, via `session_scope()`) — `fit_edge_reliability_curve`
  pool TODAS las liquidaciones sin filtrar por competicion a proposito,
  y otros tests de ese mismo fichero ya generan predicciones reales con
  cuota/edge via el pipeline completo; aislar el motor evita que ese
  volumen cambie el resultado de "por debajo del minimo" segun el orden
  de ejecucion.
- Suite completa: 401 tests, todos en verde. `ruff check backend`: cero
  errores nuevos frente al baseline antes de este cambio (comparado
  explicitamente con `git stash`).

### 12.6 Como verificarlo tras desplegar

`git pull` + `docker compose up -d --build` (el fix vive solo en
codigo Python, no requiere migracion de esquema ni regenerar
predicciones: `edge_reliability_curve` se calcula EN VIVO en cada
request a partir de las liquidaciones ya existentes, nunca se persiste
por prediccion). El caso match_id=61 (Getafe-Atletico,
`double_chance_1x`) deberia desaparecer del todo de "Oportunidades"
(score=0, no solo bajar de posicion) en cuanto haya al menos 30
predicciones liquidadas con cuota real en la BD -- que ya las hay,
confirmado en vivo durante esta misma sesion (`fit_edge_reliability_curve`
devolvio una curva ajustada real contra la BD de produccion).

### 12.7 Limitaciones que siguen existiendo

- La curva se ajusta GLOBALMENTE (todas las competiciones/mercados a la
  vez) porque asi se midio la evidencia real — no se ha probado si una
  curva por mercado seria mejor o peor con mas muestra; medirlo exige
  mas liquidaciones de las que hay hoy.
- Igual que `market_track_records`, la curva se recalcula en cada
  request desde la BD (coste de una query + un fit de `IsotonicRegression`,
  ambos baratos en la escala actual de datos) en vez de cachearse — si
  el volumen de liquidaciones crece mucho, valdria la pena revisar el
  coste.
- `real_hit_rate` es un promedio agregado de TODOS los mercados a ese
  tamanho de edge -- usarlo para poner techo al EV de un mercado
  concreto asume que el sesgo del ensemble a ese tamanho de edge es
  parecido entre mercados; no se ha medido si algun mercado especifico
  se desvia mucho de esa media.
- No se ha medido todavia el impacto de esta correccion en
  `compare-ranking-strategies` (ROI/win-rate real de v2 CON el techo de
  EV frente a v2 sin el) por falta de acceso continuo a la BD real
  desde este entorno — se recomienda correr ese comando de nuevo
  periodicamente para confirmar que la correccion tambien mejora el
  backtest agregado, no solo el caso puntual que la motivo. La leccion
  de la seccion 12.3 (un primer intento parecia razonable en tests
  sinteticos pero no bastaba en produccion real) es la razon de fondo
  para seguir verificando esto con datos reales, no darlo por cerrado.

## 13. Fase 12 (sesion 2026-11, continuacion): favorite-longshot bias

### 13.1 Problema real encontrado tras el fix de edge

Con el techo de EV por edge ya desplegado y verificado (seccion 12), el
usuario reporto un patron RELACIONADO pero distinto: "victoria local" a
cuota 5.50/6.00 (con `model_probability` de 33.5%/28.3%) seguia
destacada como "mejor oportunidad" en el dashboard. Cita textual: "una
cuota 5 o una cuota 6 no es una buena prediccion".

Es una hipotesis DISTINTA a la de edge grande: esta vez la pregunta es
si la CUOTA/PROBABILIDAD en si (independiente del tamanho del edge)
predice peor acierto -- el conocido "favorite-longshot bias" de la
literatura de apuestas deportivas (los mercados, y potencialmente un
modelo entrenado contra ellos, tienden a sobrevalorar las selecciones
poco probables).

### 13.2 Evidencia real antes de corregir

Se anhadio `backtesting/metrics.py::performance_by_odds_bucket` (mismo
patron que `performance_by_edge_bucket`, nunca una metrica nueva de
cero) y `scripts/debug_odds_vs_hitrate.py`, corrido por el usuario
contra las mismas 1133 predicciones liquidadas reales:

| cuota | n   | model_prob media | hit rate real | ROI    |
|-------|-----|-------------------|----------------|--------|
| 1-2   | 642 | 66.0%             | 68.2%          | -2.5%  |
| 2-3   | 243 | 41.0%             | 38.7%          | -10.3% |
| 3-4   | 141 | 28.4%             | 28.4%          | -2.8%  |
| 4-6   | 79  | 21.7%             | 13.9%          | -34.3% |
| 6+    | 28  | 15.8%             | 3.6%           | -77.9% |

Conclusion real, clara e inequivoca: en las cuotas 1-4 el modelo esta
razonablemente bien calibrado (`model_prob` y `hit rate` casi
coinciden); a partir de cuota 4 el modelo SOBRESTIMA su propia
probabilidad de forma creciente y dramatica (21.7% dicho vs 13.9% real;
15.8% dicho vs 3.6% real) -- confirma el "favorite-longshot bias" en
este sistema concreto, con datos reales, nunca asumido de antemano. Es
un problema DISTINTO del de edge grande: ocurre incluso con edge
modesto.

### 13.3 La correccion: segunda cota real sobre el EV

Mismo principio ya validado en la seccion 12 (nunca un blend, un TECHO
al EV basado en evidencia independiente), aplicado a una segunda fuente
de sesgo:

- **`services/evaluation_service.py::fit_probability_reliability_curve(db)`**
  (nuevo): ajusta una `IsotonicRegression` (`increasing=True`, porque la
  relacion esperada es creciente: mas `model_probability`, mas hit rate
  real) sobre `(model_probability -> resultado real)`, agregada sobre
  TODAS las competiciones/mercados a la vez (mismo motivo que
  `fit_edge_reliability_curve`). `None` con menos de
  `MIN_SETTLED_FOR_PROBABILITY_RELIABILITY` (30, mismo criterio que los
  otros `MIN_SETTLED_FOR_*`) liquidaciones con cuota real.

  **OJO, para no contradecir la seccion 11.5**: esto NO reemplaza
  `model_probability` para mostrar ni para rankear (ese camino ya se
  probo con `ensemble_calibration_report`, walk-forward y sin fuga, y
  empeoraba Brier/ECE en los 4 mercados centrales de LaLiga) -- se usa
  UNICAMENTE como otra cota conservadora sobre el EV, nunca para
  sustituir la probabilidad que ve el usuario. Tampoco contradice esa
  evidencia: aquella media calibracion POR MERCADO con walk-forward en
  el rango de probabilidad tipico de esos 4 mercados (mayormente medio);
  esta mide sesgo AGREGADO especificamente en la cola de probabilidad
  baja/cuota alta, una region que aquel estudio no aislaba.

- **`prediction/ranking.py::reality_capped_expected_value`** (extendida,
  no duplicada): ahora acepta tambien `model_probability` y
  `probability_reliability_curve`, y toma el **minimo** entre TODAS las
  cotas disponibles (la de edge Y la de probabilidad) -- cualquier
  evidencia real que diga "este EV es mas optimista de lo que deberia"
  puede bajarlo, ninguna puede subirlo. Nunca un blend.

```
ev_techo = min(
    risk_adjusted_expected_value,
    hit_rate_real_por_edge(edge) * market_odds - 1,          # seccion 12
    hit_rate_real_por_probabilidad(model_probability) * market_odds - 1,  # seccion 13
)
score_v2 = model_probability^2 * max(ev_techo, 0) * confidence
           * data_quality * market_quality_multiplier * market_track_record
           * factor_incertidumbre(ancho_intervalo)
```

- `signal_score_v2`/`rank_signals_v2`/`best_prediction_per_match` ganan
  el parametro opcional `probability_reliability_curve` (mismo patron
  que `edge_reliability_curve`: `None` es neutro). Cableado en TODOS los
  mismos puntos de produccion ya cableados en la seccion 12:
  `services/prediction_service.py`, los 5 endpoints de `/predictions/*`,
  `/matches/*`, `/history/*`, `agent/tools.py`, `agent/alerts.py` --
  cada uno calcula AMBAS curvas una vez por request/generacion (mismo
  patron que `market_track_records`).

### 13.4 Tests anhadidos

- `backtesting/test_metrics.py`: 3 tests para `performance_by_odds_bucket`
  (agrupa por rango de cuota en un dataset sintetico verificable,
  vacio sin cuotas validas, reporta Brier/ROI/hit_rate por bucket).
- `test_ranking.py`: tests para `reality_capped_expected_value` con la
  curva de probabilidad (neutro sin ella, anula un EV optimista para un
  caso "longshot" sintetico, apenas toca un favorito bien calibrado,
  toma el MINIMO cuando ambas curvas -- edge y probabilidad -- estan
  conectadas a la vez); `signal_score_v2`/`rank_signals_v2`/
  `best_prediction_per_match` con la curva de probabilidad conectada de
  extremo a extremo.
- `test_evaluation_service.py`: 2 tests para
  `fit_probability_reliability_curve` (`None` por debajo del minimo; la
  curva ajustada predice menor hit rate para probabilidad baja que para
  probabilidad alta), corridos contra el mismo motor SQLite EN MEMORIA
  aislado que ya protegia los tests de `fit_edge_reliability_curve`
  (mismo riesgo de contaminacion cruzada: tambien pool TODO sin filtrar
  por competicion a proposito).
- `scripts/debug_odds_vs_hitrate.py` + su test de extremo a extremo
  (mismo patron que `debug_edge_vs_hitrate.py`, incluida la leccion del
  `DetachedInstanceError` real: valores extraidos DENTRO del `with
  session_scope()`).
- Suite completa: 417 tests, todos en verde. `ruff check backend scripts`:
  cero errores nuevos frente al baseline antes de este cambio (comparado
  explicitamente con `git stash`).

### 13.5 Como verificarlo tras desplegar

`git pull` + `docker compose up -d --build` (sin migracion de esquema
ni regenerar predicciones -- ambas curvas se calculan EN VIVO en cada
request). Una "victoria local"/senhal con `model_probability` baja y
cuota alta (tipo cuota 5+) deberia dejar de aparecer como oportunidad
destacada en cuanto haya al menos 30 liquidaciones con cuota real en la
BD -- que ya las hay, confirmado en vivo durante esta misma sesion con
`debug_odds_vs_hitrate.py` (n=1133).

### 13.6 Limitaciones que siguen existiendo

- Igual que la curva de edge, esta se ajusta GLOBALMENTE (todas las
  competiciones/mercados a la vez) -- no se ha probado si una curva por
  mercado seria mejor o peor con mas muestra.
- Las dos curvas (edge y probabilidad) son dimensiones RELACIONADAS
  (edge grande normalmente implica probabilidad del modelo alejada de
  la del mercado) pero no identicas -- no se ha medido su correlacion
  real ni si aplicar ambos techos a la vez sobre-corrige en algun
  segmento; el minimo de ambas es deliberadamente conservador por
  diseño, pero "conservador" puede volverse "casi todo anulado" si
  ambas curvas son simultaneamente severas sobre el mismo conjunto de
  señales -- esto debe vigilarse con `compare-ranking-strategies` tras
  desplegar, no darse por bueno de antemano.
- No se ha medido el impacto agregado de las DOS correcciones juntas
  (seccion 12 + seccion 13) en `compare-ranking-strategies`
  (ROI/win-rate real), por la misma limitacion de acceso continuo a la
  BD real desde este entorno. Mismo patron que la seccion 12.6: hace
  falta seguir verificando esto con datos reales periodicamente, no
  darlo por cerrado.
- Si tras desplegar este fix el usuario reporta OTRO patron relacionado
  (p.ej. un mercado concreto, o una combinacion especifica de
  bookmakers_used/cuota), la misma disciplina aplica: pedir evidencia
  real con un diagnostico reutilizando `performance_by_*_bucket` antes
  de anhadir una tercera cota, nunca inventar un umbral mas.
