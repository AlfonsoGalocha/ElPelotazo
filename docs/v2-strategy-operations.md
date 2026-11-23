# Operación local del laboratorio

## Puesta en marcha

    docker compose up -d --build
    docker compose ps
    docker compose logs --tail=80 scheduler
    curl -fsS http://localhost:8000/publications/strategies/operations

El scheduler es un servicio de Docker y no depende del navegador. La configuración principal arranca PostgreSQL, espera a la salud de la API y aplica la política unless-stopped. PostgreSQL conserva los datos en su volumen. Una parada voluntaria del servicio no equivale a un fallo que Docker deba reiniciar. En un equipo suspendido o apagado no se ejecutan tareas. Al volver, el primer escaneo reanuda las ventanas que sigan abiertas. Una iniciada y perdida se señala como missed_capture, sin generar una decisión posterior.

## Qué ocurrió en la jornada del 20 al 23 de noviembre de 2026

El contenedor con el comparador prospectivo arrancó el 23/11 a las 12:06 hora de Madrid. La auditoría retrospectiva utilizó cuotas y predicciones archivadas de partidos que empezaron antes de ese arranque. La ausencia de cortes del laboratorio para esos partidos se explica por la fecha de despliegue. Los tres partidos que aún quedaban el día 23 tenían sus ventanas todavía cerradas. Sus históricos oficiales y su ensayo de banca de 300 € son registros de políticas distintas.

Las capturas perdidas se derivan de la hora de inicio y de los escaneos guardados. La ausencia de un escaneo registrado no distingue entre equipo apagado, parada de Docker y falta de telemetría. Un error de escaneo en una ventana tampoco demuestra, por sí solo, un fallo concreto del proveedor en ese partido. La pantalla usa razones verificables o declara causa sin confirmar.

## Frecuencias y cuotas

Un bucle comprueba las capturas aproximadamente cada diez minutos. Otro ejecuta descubrimiento de partidos, cuotas, liquidación y predicciones, con una espera de 3.600 segundos después de cada ciclo por defecto. Los resultados generales y el entrenamiento se ejecutan en los ciclos correspondientes a seis horas UTC. Las métricas prospectivas se recalculan a partir de resultados persistidos cuando se consulta el informe.

FOOTBALL_REFRESH_INTERVAL_SECONDS permite configurar la espera del ciclo principal. API_FOOTBALL_ODDS_WINDOW_DAYS limita el horizonte automático del adaptador, con siete días en Compose por defecto y quince para el adaptador fuera de ese servicio si no se configura. Dentro de cada competición se consultan primero los partidos más próximos. La aplicación descubre partidos con /fixtures y consulta /odds por partido. La frecuencia de publicación de cuotas del proveedor ronda tres horas y una consulta puede devolver la misma marca de origen. Se conserva origen, consulta y persistencia de cada cotización. El cliente conserva sus reintentos ante errores HTTP y límites temporales. [Documentación oficial de API-Football](https://www.api-football.com/documentation-v3#tag/Odds-(Pre-Match)).

Cada corte registra probabilidades, precio, casa, tiempos, versión y razones de pasar. Si faltan predicciones o precios verificables, se deja tiempo de reintento hasta los últimos quince minutos. En ese tramo puede quedar registrado un corte sin selecciones. Solo hay una fila por partido y la clave primaria impide duplicar un corte. Un precio archivado sigue sujeto a las reglas de vigencia ya existentes.

Se evaluaron observaciones adicionales a 24 y 6 horas. Aportarían una serie de cambios de precios, pero requieren una identidad de observación y reglas de cobertura diferentes. El archivo de cuotas ya conserva los movimientos capturados por el ciclo normal. Se mantuvo una única decisión oficial de laboratorio en la hora previa.

## Temperatura

El umbral de aceptación es temporal y compara Brier y Log Loss frente al 1X2 original. Los artefactos nuevos guardan los IDs de los partidos usados en los bloques. Una versión inactiva con temperatura aceptada puede proporcionar el 1X2 corregido al laboratorio mediante un cálculo en memoria para el partido, con sus features prepartido. La decisión registra la versión corregida además de la versión original. Las mezclas siguen usando el modelo original de la estrategia y el mercado independiente. Si la variante corregida usa otro artefacto, su resultado representa el candidato completo y el efecto no puede atribuirse únicamente a la temperatura. La versión activa sigue la política de promoción existente.

### Validación observada el 23/11/2026

| Liga | Versión más reciente de la comprobación | Validación n | Log Loss original → temperatura | Brier multiclase original → temperatura | Decisión |
| --- | ---: | ---: | --- | --- | --- |
| La Liga | 240 | 24 | 1,0557 → 1,0397 | 0,6084 → 0,6048 | Aceptada en sombra |
| Premier League | 241 | 21 | 1,0329 → 1,0320 | 0,6186 → 0,6183 | Aceptada en sombra |
| Serie A | 243 | 22 | 0,8846 → 0,8692 | 0,5159 → 0,5067 | Aceptada en sombra |
| Bundesliga | 242 | 63 | 1,0185 → 1,0218 | 0,6105 → 0,6114 | Respaldo original |
| Ligue 1 | 244 | 60 | 1,0880 → 1,1080 | 0,6552 → 0,6667 | Respaldo original |

Todas quedaron inactivas en producción. La mejora interna en 21–24 partidos de las tres ligas aceptadas es insuficiente por sí sola para promoción. Serie A ajustó una temperatura de 0,75 y por ello concentra su distribución, aunque mejoró ambos indicadores en el bloque posterior. La Liga y Premier ajustaron 1,15 y 1,05, respectivamente. Se comprobó en lectura que el flujo de inferencia del laboratorio devuelve una distribución que suma uno para Monza–Fiorentina, Levante–Elche y Brentford–Everton. Esta prueba operativa no creó cortes antes de sus ventanas.

Se recrearon los contenedores con las políticas de reinicio. PostgreSQL conservó el volumen elpelotazo_postgres_data, y se verificaron los hashes previos de snapshots, cuotas archivadas y cortes oficiales. Docker está habilitado al iniciar el sistema en este equipo.
