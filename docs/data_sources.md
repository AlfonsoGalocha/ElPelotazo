# Fuentes de datos

## Principio: arquitectura de adapters

Cada fuente implementa `backend/app/ingestion/base.py::DataProvider`
(`is_available()` + `fetch_matches()`). El resto del sistema nunca sabe de
donde vinieron los datos: solo ve `RawMatchRecord`.

## openfootball/football.json — FIXTURES FUTUROS, ACTIVA

- **Adapter**: `backend/app/ingestion/football_data/fixtures_provider.py::OpenFootballFixturesProvider`.
- **Fuente real**: [openfootball/football.json](https://github.com/openfootball/football.json),
  dominio publico, sin API key, auto-actualizado a diario. Publica el
  calendario COMPLETO de la temporada en curso (partidos jugados y por
  jugar) para las 5 ligas del MVP y muchas mas.
- **Por que existe ademas de Club Football Match Data**: ese dataset es
  puramente historico (solo partidos ya jugados). Sin un fixture real, no
  hay ningun partido sobre el que mostrar "predicciones de hoy" — solo se
  podria hacer backtesting sobre el pasado. Este adapter cubre exactamente
  ese hueco con partidos que de verdad se van a jugar.
- **Filtrado en `fetch_matches`**: solo se devuelven partidos SIN resultado
  Y con fecha `>= hoy`. Los partidos ya jugados de esta misma fuente se
  descartan (el dataset historico los cubre con muchisimo mas detalle:
  estadisticas de partido, cuotas). El filtro por fecha existe porque un
  dataset comunitario puede tardar en marcar un partido como jugado; sin
  ese filtro, un partido ya disputado en la realidad pero aun sin marcador
  en la fuente apareceria incorrectamente como "programado".
- **Normalizacion de equipos critica**: esta fuente usa nombres oficiales
  completos ("Real Madrid CF", "Manchester United FC"). Se anhadio un
  bloque grande de alias en `normalization/teams.py::KNOWN_ALIASES` para
  resolverlos al mismo `team_id` que el dataset historico — sin esto, cada
  equipo arrancaria en frio (cold start) en vez de usar su historial real.
  Verificado: 0 equipos nuevos sin historial tras la ingesta completa.
- **Limitacion honesta**: dataset mantenido por voluntarios, puede llevar
  retraso en aplazamientos/cambios de horario de ultima hora. No sustituye
  a una fuente oficial para apostar dinero real.

## Club Football Match Data (mirror de GitHub) — PRINCIPAL, ACTIVA, USADA PARA EL RESULTADO FINAL

- **Adapter**: `backend/app/ingestion/football_data/history_dataset.py::ClubFootballMatchDataProvider`.
- **Fuente real**: [xgabora/Club-Football-Match-Data-2000-2025](https://github.com/xgabora/Club-Football-Match-Data-2000-2025)
  (Gabor, A. 2026), un mirror publico y citable que agrega el MISMO dato de
  football-data.co.uk (resultados, estadisticas de partido, cuotas Bet365)
  en un unico CSV historico, descargable desde `raw.githubusercontent.com`.
- **Por que este y no football-data.co.uk directo**: el contenedor donde se
  desarrollo este proyecto tiene el egress de red restringido a un
  allowlist (PyPI, npm, GitHub) que no incluye football-data.co.uk pero si
  incluye GitHub. Este adapter permitio poblar la base de datos con **datos
  100% reales** (14,383 partidos, 5 ligas, 2018/19-2026/27) sin depender de
  esa restriccion.
- **Cobertura**: resultados, tiros, tiros a puerta, corners, faltas,
  tarjetas, y cuotas de Bet365 para 1X2 y Over/Under 2.5 goles (una unica
  linea; por eso `over_1_5`, `over_3_5` y `btts` no tienen
  `market_probability` real desde esta fuente — se deja `None`, nunca se
  inventa).
- **Cache**: se descarga una vez a `data/external/club_football_match_data_matches.csv`
  (~45 MB, gitignored) y se reutiliza en llamadas sucesivas.
- **Estado**: usado por defecto en `scripts/update_data.py` (`--source history_dataset`).

## football-data.co.uk directo — ALTERNATIVA, ACTIVA (requiere red sin restringir)

- **Formato**: CSV publico por temporada/liga (`mmz4281/{temporada}/{div}.csv`).
- **Cobertura**: 5 ligas objetivo desde los 90 (sobra para el requisito
  2018/19+). Incluye resultados, tiros, corners, tarjetas, faltas, arbitro y
  **cuotas de cierre de varias casas** (Bet365, Pinnacle, William Hill,
  Betfair Exchange) — mas completo en bookmakers que el mirror de GitHub.
- **Licencia/ToS**: uso personal/educativo. Sin API key. Sin SLA formal.
- **Limitaciones**: sin xG, sin posesion siempre, sin datos live, sin
  jugadores.
- **Estado**: `FOOTBALL_DATA_CO_UK_ENABLED=true` por defecto en `.env`, pero
  el adapter (`backend/app/ingestion/football_data/provider.py`) solo pudo
  probarse unitariamente contra CSVs de ejemplo en el entorno de desarrollo
  de este proyecto (la descarga real, `httpx.get` contra
  `www.football-data.co.uk`, esta bloqueada por la politica de red de ese
  contenedor). Usalo con `--source football_data_co_uk` en
  `scripts/update_data.py` si tu entorno tiene acceso de red normal — te da
  mas bookmakers para el calculo de vig que el mirror de GitHub.

## Understat — PENDIENTE, deshabilitada

xG por partido, pero requiere parsear un JSON embebido en HTML (sin API
oficial). Fragil ante cambios de maquetacion. `UNDERSTAT_ENABLED=false`.

## FBref — PENDIENTE, deshabilitada

Estadisticas muy completas via tablas HTML, pero sin API y con rate limits
estrictos (recomendado <=1 request/3s). `FBREF_ENABLED=false`.

## API-Football — cuotas de tarjetas/corners implementadas, deshabilitada por defecto; fixtures/alineaciones PENDIENTE

El adapter de FIXTURES/alineaciones (`backend/app/ingestion/api_football/provider.py`)
sigue sin implementar (plan gratuito de 100 requests/dia, inviable como
fuente historica principal; openfootball ya cubre el calendario futuro, ver
mas arriba). Lo que SI esta implementado es un adapter de CUOTAS de
tarjetas/corners, porque The Odds API no ofrece esos mercados en ningun
plan (ver seccion de abajo).

- Adapter: `backend/app/ingestion/api_football/odds_provider.py`
  (`ApiFootballOddsProvider`).
- Orquestacion: `backend/app/services/data_service.py::attach_secondary_odds_to_scheduled_matches`.
- CLI: `football-edge update-secondary-odds` (o `football-edge refresh`,
  que ya lo incluye).
- Solo pide cuotas para la JORNADA ACTUAL de cada liga (nunca la
  temporada completa: el plan gratuito son 100 requests/dia, y una
  temporada entera lo agotaria de inmediato). 1 request de fixtures +
  1 request de odds por partido de esa jornada (~10 partidos/liga).
- Busca mercados de tarjetas/corners por SUBCADENA en el nombre ("card"/
  "corner", insensible a mayusculas) y parsea "Over/Under N" por regex
  generico, en vez de asumir un nombre/formato exacto: si el nombre real
  difiere ligeramente, sigue funcionando. Si tras procesar una liga
  entera no se encuentra nada parecido a tarjetas/corners, se loguea con
  nivel WARNING la lista completa de nombres de mercado vistos, para
  ajustar el matching en un vistazo en vez de fallar en silencio.

Activacion:
1. Registrate en https://www.api-football.com/ (o via RapidAPI:
   https://rapidapi.com/api-sports/api/api-football).
2. En tu `.env`: `API_FOOTBALL_ENABLED=true` y `API_FOOTBALL_KEY=<tu-key>`.
   Si la key es de RapidAPI (no directamente de api-football.com), anhade
   tambien `API_FOOTBALL_USE_RAPIDAPI=true` (cambia el host/cabeceras de
   autenticacion, son los mismos datos).
3. `football-edge update-secondary-odds` (o `refresh`, que ya lo incluye).

**Nota de honestidad**: igual que con The Odds API, este adapter se
escribio siguiendo la documentacion publica de API-Football pero NO se
pudo verificar end-to-end contra la API real (mismo entorno con el
egress de red restringido). Los IDs de liga (`LEAGUE_IDS`) y la
estructura general de `/fixtures` y `/odds` son los documentados
publicamente, pero el nombre EXACTO de los mercados de tarjetas/corners
puede variar; el matching por subcadena y el log de diagnostico (arriba)
existen precisamente para que un desajuste se corrija en minutos en vez
de investigarse a ciegas.

## The Odds API — implementada, deshabilitada por defecto

Unica fuente de cuotas REALES para partidos FUTUROS del proyecto (los
datasets historicos usados arriba solo traen cuotas de partidos ya
jugados). Sin esto activado, las predicciones de partidos futuros
muestran `market_probability`/`edge`/`expected_value` como `null` — el
modelo sigue funcionando, simplemente no hay con que compararlo.

- Adapter: `backend/app/ingestion/odds/provider.py` (`OddsApiProvider`).
- Orquestacion: `backend/app/services/data_service.py::attach_odds_to_scheduled_matches`
  (casa cuotas con partidos ya existentes por equipo normalizado + proximidad
  de fecha; nunca crea partidos nuevos).
- CLI: `football-edge update-odds` (o `football-edge refresh`, que ya lo
  incluye antes de generar las predicciones).
- Cobertura: 1X2 (`h2h`) y Over/Under de goles a 1.5/2.5/3.5 (`totals`).
  BTTS no esta disponible en el plan usado, igual que en los datasets
  historicos (queda `None` en ambos casos, nunca inventado).
- **Tarjetas y corners NUNCA tienen cuota real con este adapter, en ningun
  plan de The Odds API**: no es un bug ni una limitacion temporal, es que
  esos mercados sencillamente no existen en su catalogo de mercados para
  futbol (que se limita a resultado y totales de goles). Para esos
  mercados hace falta la fuente API-Football descrita mas abajo; sin
  ella, quedan siempre como "prediccion del modelo — sin mercado"
  (`/predictions/model-only`) y no compiten en el ranking de "mejores
  señales".

Activacion (gratis, sin tarjeta):
1. Registrate en https://the-odds-api.com/#get-access (plan free = 500
   requests/mes).
2. Si aun no tienes `.env` (solo `.env.example`): `cp .env.example .env`.
   La app SOLO lee `.env` — editar `.env.example` directamente no tiene
   ningun efecto, y no da ningun error avisando de ello.
3. En ese `.env`: `ODDS_API_ENABLED=true` y `ODDS_API_KEY=<tu-key>`.
4. `football-edge update-odds` (o `refresh`, que ya lo incluye). Si sigue
   sin coger la key, el propio comando te dice la ruta exacta de `.env`
   que esta leyendo, para descartar ese problema.

**Nota de honestidad**: este adapter se escribio siguiendo la documentacion
publica de The Odds API, pero el entorno de desarrollo tiene el egress de
red restringido a un allowlist que no incluye `the-odds-api.com`, asi que
no se pudo verificar end-to-end contra la API real (solo contra los tests
unitarios con respuestas simuladas). Si el formato de respuesta de la API
ha cambiado, el parseo en `_parse_event()` puede necesitar un ajuste.

## Regla anti-"datos inventados"

Si una fuente no provee una variable, el campo queda `None`/`NaN`. Los
modelos manejan missing values explicitamente (`SimpleImputer`, o
simplemente ausencia de esa feature en Dixon-Coles). Nunca se rellena con un
valor inventado para "que se vea completo".
