# Arquitectura

## Principio rector

```
DATA -> FEATURES -> MODELS -> CALIBRATION -> PROBABILITY -> MARKET -> EDGE -> BACKTEST -> EXPLANATION
```

Cada flecha es un modulo Python independiente y testeable. Nada de logica
critica vive en notebooks: `notebooks/` es solo para exploracion desechable.

## Capas (`backend/app/`)

| Capa | Responsabilidad | Nunca hace |
|---|---|---|
| `ingestion/` | Descargar datos crudos de una fuente (Adapter pattern) | Normalizar nombres, calcular features |
| `normalization/` | Resolver nombres de equipo/competicion/arbitro a IDs canonicos | Tocar la red |
| `db/` | Modelos SQLAlchemy + engine/sesiones | Logica de negocio |
| `features/` | Convertir partidos en features **temporales sin leakage** | Usar datos del propio partido a predecir |
| `models/` | Modelos de goles (baseline, Poisson, Dixon-Coles, ML) + calibracion + ensemble | Saber nada de mercado ni de la BD |
| `market/` | Cuotas -> probabilidad implicita -> quitar vig | Opinar sobre si el modelo tiene razon |
| `prediction/` | Orquesta modelo + mercado -> Signal (probabilidad, edge, confianza, explicacion) | Reentrenar modelos |
| `backtesting/` | Walk-forward, metricas, reportes | Mirar al futuro |
| `services/` | Orquestacion con la BD (ingesta, entrenamiento, prediccion) | Contener matematicas de modelo |
| `api/` | FastAPI, serializacion HTTP | Logica de negocio |

## Por que esta separacion

- **Reproducibilidad**: `features/goals.py` es el UNICO punto que calcula
  features, usado tanto en entrenamiento como en prediccion en produccion.
  Evita el clasico bug de "features distintas en train y en serving".
- **Testeable**: cada capa se puede testear con datos sinteticos sin BD ni
  red (ver `backend/tests/fixtures/synthetic.py`).
- **Extensible a otros deportes**: el dominio (`Competition -> Season ->
  Match -> Team -> Market -> Prediction`) es generico; anhadir NBA/NFL/tenis
  en el futuro implica nuevos adapters de ingestion + features especificas,
  no reescribir el core.

## Diagrama de dependencias (resumen)

```
ingestion -> services.data_service -> db
                                        |
                                        v
services.match_service -> features.goals -> models.goals.* -> prediction.probability
                                                                     |
                                                                     v
                                              market.odds --> prediction.edge/confidence
                                                                     |
                                                                     v
                                                        services.prediction_service -> db -> api
```

## Estado del MVP (Fase 1)

Implementado y probado end-to-end (ver seccion "Que funciona realmente" del
README): ingestion de football-data.co.uk, normalizacion de equipos,
features de forma/fuerza/descanso, modelos baseline + Poisson + Dixon-Coles +
clasificador logistico + ensemble con peso aprendido, calibracion
(Brier/LogLoss/ECE + isotonic/Platt), mercado con eliminacion de vig,
backtesting walk-forward, API REST completa y dashboard Next.js.

No implementado (fases futuras, ver `docs/development.md` y el roadmap del
brief): tarjetas, corners, tiros, jugadores, arbitros como feature de
tarjetas, datos live, Monte Carlo, discovery engine, alertas.
