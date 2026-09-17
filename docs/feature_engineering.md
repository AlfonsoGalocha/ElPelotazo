# Feature Engineering

## Regla de oro anti-leakage

Para el partido `i` de un equipo, cualquier feature solo puede usar filas
con `date < fecha(i)`. Se garantiza mecanicamente: `features/base.py` aplica
`shift(1)` antes de cualquier `rolling`/`expanding`, por lo que la fila del
propio partido nunca puede entrar en su propia ventana. Ver
`backend/tests/unit/test_features_leakage.py` para las pruebas automatizadas
de esta garantia (incluye un test que muta un resultado futuro y comprueba
que las features de partidos anteriores no cambian ni un bit).

## Pipeline (`features/goals.py::build_match_feature_table`)

1. `build_team_match_long` (`features/base.py`): partidos (ancho, 1 fila =
   1 partido) -> formato largo (1 fila = 1 equipo en 1 partido).
2. `compute_form_features` (`features/form.py`): medias moviles de
   goles/tiros/corners/xG para ventanas 3/5/10 y "temporada completa"
   (expanding), siempre con `shift(1)`.
3. `compute_home_away_split_features` (`features/home_away.py`): la misma
   idea pero condicionada a jugar en casa/fuera, para separar "rendimiento
   global" de "rendimiento como local/visitante".
4. `compute_rest_days` (`features/rest.py`): dias desde el partido anterior
   (proxy de fatiga/calendario apretado).
5. `compute_strength_features` (`features/strength.py`): rating dinamico
   estilo Elo de ataque/defensa, actualizado partido a partido en orden
   cronologico estricto (el rating usado en el partido `i` es el que existia
   ANTES de jugarse `i`).
6. Merge de vuelta a formato ancho (`home_*` / `away_*`) + columnas de
   diferencia (`*_difference`).

## Cold start / equipos ascendidos (secciones 57-59 del brief)

- Un equipo sin historial en el dataset de entrenamiento (recien ascendido,
  o simplemente el primer partido de la temporada 2018/19) recibe
  `attack_strength`/`defense_strength` = media global del pool de equipos
  entrenados (shrinkage), en vez de un rating arbitrario o un crash.
  Implementado en `DixonColesModel._team_params`.
- Las features de forma (`*_avg_last5`, etc.) quedan `NaN` cuando no hay
  historial previo suficiente; los modelos ML usan `SimpleImputer` (mediana)
  y Dixon-Coles/Poisson manejan la ausencia via el propio rating de equipo.

## Pendiente (fases futuras)

- `features/referee.py`: tasa de tarjetas por arbitro — implementado pero
  usado solo cuando se aborde Fase 2 (mercados de tarjetas).
- `features/cards.py`, `features/corners.py`, `features/shots.py`,
  `features/players.py`: stubs documentados, `NotImplementedError`
  explicito, para Fases 2-5.
