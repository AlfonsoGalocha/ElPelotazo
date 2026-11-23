# Calibración coherente y laboratorio prospectivo

## Calibración y promoción

El nuevo formato de artefacto conserva la compatibilidad con los modelos existentes. En entrenamiento, se toma el bloque temporal reservado para calibración. Se ajusta una temperatura conjunta del 1X2 en el tramo inicial, con penalización logarítmica hacia una temperatura de 1. Se evalúa en un tramo cronológicamente posterior. Se exige un mínimo de 60 observaciones en total, 30 para el ajuste y 20 para la validación. La temperatura se acepta cuando reduce Log Loss y no empeora el Brier multiclase frente a la distribución original. La isotónica marginal renormalizada se registra como referencia diagnóstica.

La proyección usa potencias de las probabilidades, con un mínimo numérico, y conserva la suma de uno. Se deriva la doble oportunidad del 1X2. Se usa el lado Over validado de cada par que existe en el catálogo, goles 2,5 y córners 9,5, y se obtiene Under por complemento. Si falta ese calibrador o es degenerado, se usa respaldo original. Ambos marcan se modela como «Sí». «No» es su complemento matemático y no tiene una fila de predicción independiente en el catálogo actual. El laboratorio requiere ambos lados cotizados para quitar el margen. Las líneas 8,5 y 10,5 de córners se tratan como mercados separados.

Para conocer el origen del cálculo, los artefactos nuevos registran el diagnóstico de validación y las predicciones nuevas guardan calibration_source. Los artefactos antiguos siguen operando con la ruta activa previa. En modo sombra se usa el 1X2 original proyectado cuando el artefacto no ofrece temperatura aceptada. Los calibradores de otros mercados con fecha no certificada se excluyen del cálculo corregido.

Referencia metodológica: [Guo et al., On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html).

### Versiones 185 y 187

| ID | Fecha de creación UTC | Final declarado | Holdout declarado | Artefacto |
| --- | --- | --- | --- | --- |
| 185 | 2026-11-18 09:31 | 2027-05-22 | 2025/26 | Bundesliga |
| 187 | 2026-11-18 09:33 | 2027-05-29 | 2025/26 | Ligue 1 |

Los joblib locales se abrieron correctamente. Sus entradas son baseline, Dixon–Coles, clasificador, pesos, calibradores y modelo de córners. No contienen un listado de partidos de entrenamiento o evaluación. En la tabla de versiones solo consta holdout_season en los hiperparámetros de estas dos filas. El código actual excluye fechas futuras, pero ese código no acredita lo que entró en un artefacto previo. La evidencia disponible no identifica si fue un error de metadatos o de corpus. La ruta de promoción devuelve uncertified_training_cutoff si falta una fecha o el final declarado es posterior a la creación. Los artefactos y sus registros históricos se conservan.

## Captura y evaluación

El scheduler ejecuta capture-strategies cada diez minutos. Captura cada partido de las cinco ligas una vez, durante la última hora antes del inicio. La clave primaria de strategy_captures es match_id. Las repeticiones usan INSERT con conflicto ignorado. La tabla es aditiva y se crea por el mecanismo de arranque existente. La evaluación lee el resultado final sin sobrescribir las decisiones. Para anulaciones del partido usa estado void. Córners sin conteo verificable quedan pendientes de liquidación.

Cada estrategia ve las mismas predicciones y cotizaciones vigentes. Se usa solo la cuota activa de mercado exacto que coincide con su registro archivado. El origen precede a consulta, registro y corte. La frescura usa la configuración existente de cuotas. Para el mercado independiente se exigen dos casas distintas de la que cotiza el precio evaluado. Se normalizan las inversas por casa sobre todos los lados y se promedian las probabilidades. Los lados de una misma casa deben tener origen dentro de 15 minutos y quedar a menos de una hora de la oferta evaluada. Se guardan los IDs, el número de casas y el intervalo de origen.

Se calculan las ocho estrategias sobre el mismo corte. La probabilidad mínima se configura con SHADOW_MIN_PROBABILITY y vale 0,65 de serie. El rango principal del laboratorio es 1,35–1,90. Los cinco rangos de prueba conservan sus propios resultados. La puntuación pondera probabilidad al 40 %, EV acotado al 30 % y evidencia al 30 %. La evidencia incorpora confianza, calidad, frescura e incertidumbre. Solo se toma una decisión por regla, rango y partido, o se registra ausencia de selección.

GET /publications/strategies?band=principal devuelve comparativa, desgloses, curva, incertidumbre e historial. El Brier y Log Loss usan únicamente mercados observados con pronóstico de las ocho estrategias, con una observación por partido y mercado. La evaluación económica usa las decisiones efectivamente seleccionadas en cada variante. Los intervalos de ROI y Brier remuestrean fechas completas cuando hay cinco o más bloques. Son descriptivos y conservan la dependencia intrapartido. El cálculo monetario usa apuestas virtuales de 10 € y el orden del inicio para el drawdown.

El laboratorio distingue las decisiones nuevas de los backtests y de la estrategia activa. Los resultados prospectivos necesitarán partidos futuros y liquidaciones reales. La validación económica requiere varias jornadas completas, cobertura comparable de cuotas, y un período de evaluación posterior al ajuste de la regla.
