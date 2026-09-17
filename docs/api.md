# API

FastAPI, documentacion interactiva automatica en `/docs` (Swagger) y
`/redoc` cuando el servidor esta corriendo.

## Endpoints

| Metodo | Ruta | Descripcion |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/competitions` | Lista las 5 ligas del MVP |
| GET | `/teams` | Lista equipos normalizados |
| GET | `/matches` | Lista partidos (filtros: `competition_code`, `date_from`, `date_to`) |
| GET | `/matches/{id}` | Detalle de un partido |
| GET | `/matches/{id}/predictions` | Las 5 predicciones de mercado de un partido |
| GET | `/predictions/today` | Todas las predicciones de partidos de hoy |
| GET | `/predictions/top-signals` | Ranking transparente (ver formula abajo) |
| GET | `/predictions/{id}` | Una prediccion concreta |
| GET | `/backtests` | Historico de backtests ejecutados |
| POST | `/backtests/run` | Ejecuta un backtest walk-forward `{"competition_code", "market"}` |
| GET | `/models` | Lista de `ModelVersion` entrenados |
| GET | `/models/{id}` | Detalle de un `ModelVersion` |
| POST | `/models/train` | Entrena/re-entrena una competicion `{"competition_code"}` |

## Formula de `/predictions/top-signals` (seccion 38)

```
score = edge * confidence * data_quality   (solo si edge > 0)
```

Deliberadamente NO es "ordenar por edge". Un edge del 20% de un modelo con
confidence 0.2 (mal calibrado historicamente, o basado en 3 partidos) queda
por debajo de un edge del 8% con confidence 0.85. La formula esta en
`backend/app/api/routes/predictions.py::top_signals`, no oculta en ningun
sitio.

## Forma de una `Prediction` (respuesta)

```json
{
  "id": 1,
  "match": { "home_team": {...}, "away_team": {...}, "competition": {...} },
  "market": "over_2_5",
  "model_probability": 0.634,
  "market_probability": 0.551,
  "market_odds": 1.82,
  "fair_odds": 1.58,
  "edge": 0.083,
  "expected_value": 0.145,
  "confidence": 0.81,
  "data_quality": 0.74,
  "signal_tier": "HIGH_DATA_SUPPORT",
  "explanation": [
    {"feature": "...", "display_name": "...", "contribution": 0.42, "direction": "increases_probability"}
  ]
}
```
