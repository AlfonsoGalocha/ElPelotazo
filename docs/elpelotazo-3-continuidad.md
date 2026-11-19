# ElPelotazo 3.0

19 de noviembre de 2026. La rama local `codex/elpelotazo-3` parte de `9796f9a`, la entrega local de 2.0. Se reutilizaron el consenso de cuotas, la política de calidad, la liquidación de córners, los snapshots y la infraestructura de calibración y promoción. Esta rama todavía no está desplegada. Producción se consultó en solo lectura.

## Evidencia del estado inicial y del cambio

| Comprobación | Resultado |
| --- | --- |
| Liquidaciones de partidos finalizados registradas tras el inicio, producción | 5.405 |
| Liquidaciones con conteos de córners ausentes, producción | 944 |
| Capturas de predicciones en producción al realizar la consulta | 29.811 |
| Bundesliga | 185 vigente, 208 inactiva |
| Ranking 2.0 en copia de producción, misma ventana de 30 días y límite de 10 | 0 |
| Ranking relativo 3.0 en la misma copia | 10 selecciones, 10 partidos distintos |
| Selecciones de ese Top 10 con valor acreditado | 0 |
| Pronósticos oficiales liquidados en la copia al migrar | 0; ROI y calibración de esa muestra sin estimar |

Entre las primeras selecciones de esa prueba se encontraron córners a 1,49, 1,65 y 1,40. La hora de origen de sus cuotas heredadas seguía sin verificar. El rango de 1,35 a 1,90, la diversidad y el estado de valor se comprobaron sobre las respuestas del backend en PostgreSQL. El total de 10 describe cobertura del ranking; no es una mejora demostrada de rentabilidad.

## Selecciones disponibles y recomendaciones

`GET /predictions/available` devuelve precios representados en el consenso dentro de 1,35 a 1,90, ambos incluidos, y excluye el precio justo derivado de doble oportunidad. El orden es lexicográfico. Usa primero la probabilidad conservadora y la calibrada o cruda disponible, seguidas de EV, evidencia histórica, calidad y frescura. El edge aparece como contexto. El valor estadístico aplica por separado el gate estricto v3, con origen verificable, calibración, incertidumbre, fiabilidad y EV conservador positivo. Fijar un filtro opcional de la interfaz restringe la consulta solicitada.

La diversidad admite una selección por encuentro por defecto y descarta familias prácticamente equivalentes cuando se solicitan varias. Agrupa las líneas de totales y las de córners, y agrupa 1X2 con doble oportunidad. Es una aproximación por familias, sin estimación empírica de correlaciones entre mercados distintos.

`scripts/compare_publication_rankings.py --from-date AAAA-MM-DD --before-date AAAA-MM-DD` compara cinco estrategias fijas sobre candidatas congeladas y un intervalo posterior explícito. Selecciona primero cada candidata y después busca su resultado. Devuelve n, acierto, ROI teórico, Brier, Log Loss y drawdown por estrategia y segmentos. La reproducción es contrafactual y no se incluye en el ROI oficial. En la copia migrada las cinco muestras eran de tamaño cero. Se mantiene `relative-v1` como estrategia de partida, sin afirmar que es superior fuera de muestra.

## Qué cuenta como acierto

`fixture_publications` guarda la primera generación o lectura de caché disponible en la última hora antes del inicio registrado por fixture. La inserción es idempotente por partido. El scheduler comprueba los partidos cada 10 minutos independientemente del ciclo existente de actualización de cuotas. El capturador comprueba el estado programado, el corte y el timestamp de generación. Las revisiones adicionales dentro de la ventana se guardan en `fixture_ranking_revisions`. Las estadísticas oficiales conservan la primera captura cuando llegan revisiones. La política puede dejar un partido sin publicación si faltan predicciones, la generación falla o se interrumpe el scheduler.

Los grupos iniciales son 1X2, goles 2,5 y córners 9,5. Se fija un principal por grupo según la probabilidad cruda más alta. En 1X2 esto conserva el vector mutuamente excluyente, mientras que las probabilidades calibradas se guardan aparte. El laboratorio usa Brier multiclase como suma de errores cuadrados de las tres clases y Log Loss multiclase con la clase que ocurre. En binarios usa la probabilidad del lado canónico. Cada grupo muestra su calibración, Brier, Log Loss, segmentos y tamaños de muestra.

Una prueba automatizada reproduce Real Madrid 65 %, empate 20 %, Atlético 15 %, con empate final. El principal local falla y el 20 % del empate contribuye al Brier y a la calibración. Si se había registrado una recomendación local, la pérdida oficial es una unidad. Si no había ninguna recomendación, el resultado teórico es cero y el ROI queda sin estimar. La cuota 5 del empate nunca genera beneficio. Las anuladas se cuentan por separado. En córners se requiere un total verificable y el scheduler puede reconciliar hasta cinco fixtures publicados por ciclo mediante API‑Football, con espera de 24 horas tras una respuesta archivada sin evidencia suficiente.

## Base, endpoints e interfaz

La migración [002_fixture_publications.sql](../backend/migrations/002_fixture_publications.sql) crea las dos tablas y sus triggers append-only. Se aplica después de 001 porque reutiliza su función de protección del histórico. No rellena publicaciones retrospectivas.

- `GET /publications` devuelve historial oficial, resultados de principales, calibración y rendimiento de recomendaciones. El historial admite liga, jornada, mercado, estado, modelo y rango de probabilidad.
- `GET /publications/{match_id}` devuelve la captura, sus probabilidades y variables vinculadas cuando se conservaron.
- `GET /publications/{match_id}/revisions` devuelve revisiones del ranking.
- `GET /predictions/available` alimenta el Top 5 o Top 10 y conserva la indicación de valor de cada selección.

El Laboratorio muestra tarjetas por grupo, sus diagramas de calibración y resultados por competición y versión. Las pestañas del histórico anterior quedan identificadas como una muestra distinta. Histórico de predicciones permite consultar la captura original, alternativas y variables. Rendimiento de oportunidades muestra stake teórico fijo, ROI, beneficio, máximo drawdown, curva y desgloses con muestra. Las muestras vacías tienen un mensaje explícito. Las tarjetas históricas conservan sus registros y las nuevas capturas y métricas no las usan.

## Pruebas y continuidad

En PostgreSQL 16, ambas migraciones se aplicaron sobre una copia real. Para la prueba de captura se adelantó un kickoff únicamente en esa copia. El fixture interno 1323 archivó 1X2, goles 2,5 y córners 9,5, con una publicación oficial y dos revisiones. PostgreSQL rechazó un UPDATE sobre la publicación. Las dos rutas de lectura devolvieron HTTP 200 con API‑Football bloqueada y sin llamar al proveedor. Esta prueba de almacenamiento usa un escenario controlado y se mantuvo fuera de las cifras de rendimiento real.

La suite completa final de backend terminó con `602 passed, 8128 warnings in 123.15s (0:02:03)`. También pasaron lint de Next.js, TypeScript, el build de producción y la validación de Docker Compose. Durante la implementación se corrigieron una incidencia de lint en una etiqueta previa y un error de sintaxis intermedio en el detalle de publicaciones, con nuevas ejecuciones completas tras la corrección.

Pendientes: verificar que el inicio registrado corresponda siempre al inicio real, medir la cobertura efectiva del scheduler a lo largo de varias jornadas, acumular capturas representativas y medir el ranking y la promoción fuera de muestra. La reconciliación anterior de los 944 córners no se ejecutó globalmente. Faltan evolución histórica con muestra oficial suficiente, comparaciones prospectivas de candidatos en paralelo y análisis específicos de errores como recién ascendidos. No se activó ni la 208 ni ningún candidato. Producción y GitHub conservan su estado.

El listado exacto de archivos de la fase se obtiene con `git diff --name-only 9796f9a..HEAD`. Las áreas principales son `backend/app/prediction/available_ranking.py`, `backend/app/services/publication_service.py`, las rutas y modelos de publicaciones, `backend/migrations/002_fixture_publications.sql`, el scheduler y las páginas de frontend.
