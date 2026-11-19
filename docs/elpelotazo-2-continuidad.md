# ElPelotazo 2.0 — entrega de la primera fase

19 de noviembre de 2026. Rama `codex/elpelotazo-2`, basada en el código local de producción `b5eb438`. Las consultas a producción fueron de solo lectura. Las migraciones, la persistencia de pruebas y el entrenamiento se ejecutaron en bases aisladas. Las horas de proveedor y almacenamiento de esta auditoría se informan en UTC.

## Hallazgos y cambios

| Evidencia | Antes | Después en la copia de producción |
| --- | ---: | ---: |
| Claves idénticas duplicadas de cuotas prepartido vigentes | 568 | 0 |
| Filas de cuotas vigentes | 111.253 | 110.685 |
| Filas en el histórico de cuotas | 870.690 | 870.690 tras la migración |
| Snapshots de predicción, respuestas, clasificación y variables por equipo | 22.743 / 609 / 576 / 1.718 | Los mismos cuatro recuentos tras la migración |
| Segmentos del informe de rendimiento | 245 | 15 con timestamp anterior al inicio y liquidación verificable según la política actual |
| Predicciones liquidadas de partidos finalizados, registradas tras el inicio | 5.405 | Conservadas. Excluidas del informe activo y de los ajustes de fiabilidad |
| Oportunidades a 30 días, mismo conjunto de datos | 24 | 0 con cuotas heredadas |
| Candidatas con origen de cuota sin verificar, después de aplicar los filtros previos | El diagnóstico no distinguía el origen | 385, de las cuales 98 tienen EV estimado positivo y figuran en observación |
| Captura histórica de LaLiga antes de 09:00 UTC | Sin ruta pública | 20 equipos recuperados de la captura previa |
| Córners liquidados con conteo ausente | 944 selecciones en 236 partidos | En la copia se verificaron las 4 selecciones de un partido con una respuesta auténtica. Sin backfill general |

El informe de rendimiento etiqueta su muestra como `pre_kickoff_timestamp_screened`. El timestamp y la verificación de la etiqueta no certifican la disponibilidad histórica de todos los insumos ni el instante del pitido real. El archivo muestra el estado de procedencia de cada predicción. El aprendizaje de fiabilidad se ajusta con las liquidaciones filtradas temporalmente, con las limitaciones descritas abajo. Los mercados de tarjetas quedan fuera de las rutas de oportunidades y de la evidencia de rendimiento. Sus registros se conservan en el histórico.

## Frescura, integridad e histórico

La ingesta de API‑Football captura la hora de origen del payload cuando hay zona horaria y conserva el momento de descarga separado de `recorded_at`. La creación de predicciones utiliza cuotas prepartido del mercado y línea exactos y congela la hora de origen más antigua del conjunto que entró al consenso. El filtro principal requiere una hora verificable, calibración e intervalo de incertidumbre. Su frescura inicial se configura con `OPPORTUNITY_MAX_ODDS_AGE_MINUTES` (360 minutos) o con `MAX_ODDS_AGE_MINUTES` cuando se define. Las seis horas son un punto de partida operativo. El muestreo observó un caso de más de ocho horas e informa las exclusiones para ajustar el parámetro con más evidencia.

La prueba de extremo a extremo reutilizó el payload real #364 para el partido #1323 en una copia de PostgreSQL. Conservó la hora de proveedor 03:17:15 UTC pese a la ingesta de la prueba a las 13:17 UTC. El filtro devolvió `cuota_desactualizada`. La predicción BTTS se vinculó a ocho capturas, todas del mismo mercado y sin línea. Una segunda generación archivó la primera como snapshot #22815, con los mismos ocho enlaces, su versión de variables y el hash del modelo.

Cada cuota vigente nueva se vincula a su captura de `match_odds_history`. Las nuevas predicciones guardan los IDs de los insumos del consenso y el SHA‑256 del artefacto cargado. Las variables guardadas quedan vinculadas por la versión del snapshot y también se archivan las derivadas compartidas. Esas referencias sobreviven cuando se sustituye la predicción. Las entradas antiguas sin referencias permanecen incompletas.

La migración [001_p0_evidence.sql](../backend/migrations/001_p0_evidence.sql) es explícita para PostgreSQL 15 o superior. Añade columnas y la tabla de evidencia de córners. Solo colapsa duplicados de igual precio de `match_odds` vigente. Si encuentra precios distintos para una misma clave, la creación del índice falla y revierte la transacción. El índice contempla líneas nulas. Los triggers rechazan UPDATE, DELETE y TRUNCATE de las capturas históricas. Para mantenimiento que deba corregir evidencia, la operación ordinaria es añadir una observación. Una reparación de esquema extraordinaria requiere el propietario de PostgreSQL y una transacción que controle y restablezca los triggers. Los comandos de borrado de fixtures con datos históricos pueden fallar y quedan fuera de la operación ordinaria.

Se probaron en PostgreSQL aislado el borrado de duplicados, el rechazo de cambios históricos y la inserción de nuevas capturas. Al simular la caída del proveedor, se verificaron con datos de producción copiados las rutas del histórico, cuadros de clasificación, cuotas y predicciones archivadas y rendimiento. Hubo cero llamadas externas durante las lecturas. El diagnóstico funciona en transacciones de solo lectura.

## Calibración y candidatos

El modelo base solo se ajusta con partidos finalizados anteriores al holdout. El holdout separa selección de modelos y pesos, ajuste del calibrador y comparación temporal del calibrador. Se mantienen juntos los partidos con el mismo timestamp en los cortes. El metadato de entrenamiento cubre tanto el ajuste del modelo como las fechas empleadas para selección y calibración. El diagnóstico de comparación usa partidos posteriores al ajuste y a la creación de ambas versiones, métricas pareadas por mercado e intervalos bootstrap agrupados por partido. Una reproducción con los datos actuales no activa versiones.

Se entrenó una versión aislada de Bundesliga. El modelo base usó fechas hasta el 17 de mayo de 2025 y la última observación de ajuste o selección del artefacto correspondía al 16 de mayo de 2026. El holdout tenía 306 partidos. Se usaron 118 para selección, 53 para ajuste inicial del calibrador y 135 para la comprobación. Solo BTTS aceptó isotónica.

| BTTS, comprobación temporal del calibrador | Probabilidad cruda | Isotónica |
| --- | ---: | ---: |
| Brier | 0,231322 | 0,227449 |
| Log Loss | 0,655187 | 0,647618 |
| ECE | 0,082411 | 0,069959 |

Los resultados por rango se registran en `metrics.calibration.temporal_diagnostics`, con tamaño y frecuencia empírica. La versión entrenada en la copia quedó inactiva. El chequeo frente a la 185 devolvió cero partidos independientes. Las versiones reales 185 (vigente) y 208 (inactiva) conservaron sus estados. Las cifras anteriores son diagnóstico de calibración y no demuestran una mejora prospectiva frente a la 185.

## Córners

El fixture de API‑Football `1575216` (partido interno 696) devolvió 5 córners de Freiburg y 1 de Union Berlin. Tras archivar esa respuesta auténtica en la copia de PostgreSQL, la API confirmó las cuatro liquidaciones originales: over 8,5, 9,5 y 10,5 fallaron, y under 9,5 acertó. Las liquidaciones originales se conservaron y la evidencia quedó vinculada al payload. Con datos incompletos o un resultado discrepante, el histórico informa del motivo y el resultado no contribuye al informe activo.

`python -m scripts.reconcile_corners MATCH_ID` consulta un único fixture sin guardar datos. Con `--persist` añade la respuesta y la observación a la base de ejecución. El comando no hace una consulta global, para poder administrar la cuota del proveedor.

El script [audit_corners_temporal.py](../scripts/audit_corners_temporal.py) reprodujo un corte cronológico 70/30 por liga para el Poisson actual y una binomial negativa con dispersión estimada solo en entrenamiento. Evalúa las cuatro líneas existentes y publica Brier, Log Loss, ECE y acierto. Tabla de ejemplo para más de 9,5 córners:

| Liga | Partidos de prueba | Brier Poisson / NB | Log Loss Poisson / NB | ECE Poisson / NB |
| --- | ---: | --- | --- | --- |
| Bundesliga | 738 | 0,253977 / 0,253804 | 0,706424 / 0,705941 | 0,053542 / 0,052299 |
| LaLiga | 922 | 0,257263 / 0,257109 | 0,710222 / 0,709849 | 0,070650 / 0,069002 |
| Ligue 1 | 821 | 0,253949 / 0,253741 | 0,701778 / 0,701316 | 0,059024 / 0,055639 |
| Premier League | 922 | 0,246105 / 0,245911 | 0,685322 / 0,684868 | 0,076420 / 0,070047 |
| Serie A | 918 | 0,250941 / 0,250228 | 0,695210 / 0,693694 | 0,064583 / 0,057641 |

Los cortes usan valores reconstruidos con PostgreSQL actual. El cambio medio es pequeño y carece de intervalos por fixture en esta prueba. Se conserva Poisson. No se calculó ROI sin cuotas prepartido verificables y asociadas a estos mismos pronósticos.

## Pruebas ejecutadas

La última ejecución completa del backend, sobre SQLite temporal, terminó con `598 passed, 8128 warnings in 126.24s (0:02:06)`. Entre las advertencias predominan variables ausentes en fixtures y deprecaciones del entorno. Antes de corregir los fixtures y las regresiones intermedias se registraron fallos de suite. PostgreSQL 16 quedó verificado de forma separada con una base aislada y con copias de datos reales. TypeScript y el build optimizado de Next.js terminaron correctamente.

## Interfaz

En Oportunidades, el panel plegable «Diagnóstico de oportunidades» muestra partidos, mercados, EV, selecciones que superan el filtro, observación y motivos de exclusión. La antigüedad de la cuota usa la fuente y el historial muestra «Córners sin verificar» o «Liquidación discrepante» cuando corresponde. Rendimiento identifica la muestra por el timestamp de registro. Estos cambios usan la interfaz y los componentes del repositorio. La comprobación de frontend cubrió TypeScript y el build de producción.

## Archivos y continuidad

- Persistencia e ingesta en `backend/app/db/models`, `backend/app/services/data_service.py`, `backend/app/ingestion/api_football/odds_provider.py` y la migración 001.
- Evidencia temporal, liquidación, ranking y reproducción en `backend/app/prediction/eligibility.py`, `backend/app/services/evaluation_service.py`, `backend/app/services/prediction_service.py` y `backend/app/prediction/ranking.py`.
- Entrenamiento y promoción en `backend/app/services/model_service.py`, `backend/app/services/promotion_service.py` y el script de comparación de córners. Rutas de consulta en `backend/app/api/routes`.
- Pruebas de regresión en `backend/tests` y cambios de presentación en `frontend/app`, `frontend/components` y `frontend/lib/api.ts`. `git diff --name-only b5eb438..HEAD` enumera los archivos.

La primera fase queda disponible en la rama local. Sigue pendiente acreditar el pitido real y la disponibilidad temporal de cada insumo, separar la fiabilidad estrictamente prospectiva de la muestra filtrada, capturar predicciones paralelas de candidato y producción, reconciliar los demás fixtures dentro de la cuota del proveedor, vincular inequívocamente el payload a cada cuota de consenso y reproducir artefactos históricos incompletos. Tampoco se ha validado una estrategia nueva de ranking con ROI, desplegado una nueva distribución de córners ni completado el rediseño integral de gráficos. El estado de la 208 se mantiene.
