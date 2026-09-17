# Fuentes de datos

## Principio: arquitectura de adapters

Cada fuente implementa `backend/app/ingestion/base.py::DataProvider`
(`is_available()` + `fetch_matches()`). El resto del sistema nunca sabe de
donde vinieron los datos: solo ve `RawMatchRecord`.

## football-data.co.uk — PRINCIPAL, ACTIVA

- **Formato**: CSV publico por temporada/liga (`mmz4281/{temporada}/{div}.csv`).
- **Cobertura**: 5 ligas objetivo desde los 90 (sobra para el requisito
  2018/19+). Incluye resultados, tiros, corners, tarjetas, faltas, arbitro y
  **cuotas de cierre de varias casas** (Bet365, Pinnacle, William Hill,
  Betfair Exchange).
- **Licencia/ToS**: uso personal/educativo. Sin API key. Sin SLA formal.
- **Limitaciones**: sin xG, sin posesion siempre, sin datos live, sin
  jugadores.
- **Estado**: `FOOTBALL_DATA_CO_UK_ENABLED=true` por defecto.

### Nota sobre el entorno de desarrollo de este proyecto

El contenedor donde se construyo este MVP tiene el egress de red restringido
a un allowlist (PyPI, npm, GitHub) que **no incluye football-data.co.uk**. El
adapter (`backend/app/ingestion/football_data/provider.py`) esta
implementado y probado unitariamente contra CSVs de ejemplo
(`FootballDataCoUkProvider.parse_csv`), pero la descarga real
(`fetch_matches`, que hace `httpx.get`) solo pudo probarse con datos
sinteticos en este entorno. **Ejecuta `python scripts/update_data.py` en un
entorno con acceso a internet sin restringir** para poblar la base de datos
con partidos reales.

## Understat — PENDIENTE, deshabilitada

xG por partido, pero requiere parsear un JSON embebido en HTML (sin API
oficial). Fragil ante cambios de maquetacion. `UNDERSTAT_ENABLED=false`.

## FBref — PENDIENTE, deshabilitada

Estadisticas muy completas via tablas HTML, pero sin API y con rate limits
estrictos (recomendado <=1 request/3s). `FBREF_ENABLED=false`.

## API-Football — PENDIENTE, deshabilitada

Plan gratuito de 100 requests/dia: inviable como fuente historica principal.
Util a futuro para fixtures del dia / alineaciones. Requiere
`API_FOOTBALL_KEY`.

## The Odds API — PENDIENTE, deshabilitada

Para cuotas de partidos FUTUROS (no historicas). Requiere `ODDS_API_KEY`.

## Regla anti-"datos inventados"

Si una fuente no provee una variable, el campo queda `None`/`NaN`. Los
modelos manejan missing values explicitamente (`SimpleImputer`, o
simplemente ausencia de esa feature en Dixon-Coles). Nunca se rellena con un
valor inventado para "que se vea completo".
