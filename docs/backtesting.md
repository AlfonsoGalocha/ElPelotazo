# Backtesting

## Walk-forward, nunca split aleatorio

`backtesting/splits.py::expanding_window_splits` genera folds donde el train
es TODO el pasado acumulado y el test es la temporada siguiente:

```
Fold 1: train = [S1, S2]        test = S3
Fold 2: train = [S1, S2, S3]    test = S4
Fold 3: train = [S1..S4]        test = S5
```

Tambien existe `rolling_window_splits` (ventana de tamanho fijo) para
comparar sensibilidad a "cuanto pasado usar". Ambos estan testeados
(`backend/tests/backtesting/test_walk_forward.py`) para garantizar que:
- El train nunca "ve" el test (indices disjuntos).
- La fecha maxima del train es siempre anterior a la fecha minima del test.

## Motor (`backtesting/engine.py`)

Para cada fold: entrena un modelo **nuevo** (nunca reutiliza estado entre
folds), calcula la matriz de marcador para el test, deriva la probabilidad
del mercado pedido y la compara contra el resultado real. Se registra
tambien que temporadas exactas se usaron en cada fold test (`test_seasons`).

## Metricas: Model Quality vs Market Strategy (seccion 27)

`backtesting/metrics.py` separa explicitamente:

- **`model_quality_metrics`**: Brier, Log Loss, ECE, curva de fiabilidad,
  accuracy (solo secundaria). No depende de que existan cuotas.
- **`market_strategy_metrics`**: ROI hipotetico, yield, edge medio, max
  drawdown — SOLO se calcula si hay cuotas de mercado disponibles para esas
  predicciones, y se etiqueta explicitamente como simulacion hipotetica
  ("resultados pasados no garantizan resultados futuros", seccion 54).
- **`performance_by_probability_bucket`**: agrupa por rango de probabilidad
  predicha (50-55%, 55-60%, ...) y compara contra la frecuencia empirica
  real — la forma correcta de verificar calibracion en la practica.

## Calibracion antes/despues (`backtesting/calibration.py`)

`compare_calibration` separa el dataset en validacion (ajusta el
calibrador) y test (mide el efecto), para no medir la mejora sobre los
mismos datos usados para ajustarlo.

## Deteccion de leakage (seccion 14/36)

Tests dedicados en `backend/tests/unit/test_features_leakage.py`:
1. Una ventana `avg_last5` para el 6º partido de un equipo debe coincidir
   exactamente con la media manual de sus 5 partidos previos.
2. Mutar el resultado del ULTIMO partido del dataset no debe cambiar NI UN
   BIT las features de partidos anteriores (comparacion exacta con
   `pandas.testing.assert_frame_equal`).
3. El primer partido de un equipo en el dataset debe tener features de
   forma `NaN` (no "inventa" un historial).

## Como ejecutar

```bash
python scripts/run_backtest.py laliga --market over_2_5
# escribe data/processed/backtest_laliga_over_2_5.json
```

O via API: `POST /backtests/run {"competition_code": "laliga", "market": "over_2_5"}`.
