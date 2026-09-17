"""Importa todos los modelos para que Base.metadata los conozca."""

from backend.app.db.models.core import (  # noqa: F401
    Competition,
    DataSource,
    Player,
    Referee,
    Season,
    Team,
    TeamNameMapping,
)
from backend.app.db.models.features import TeamMatchFeatures  # noqa: F401
from backend.app.db.models.matches import (  # noqa: F401
    Match,
    MatchOdds,
    MatchStatistics,
    PlayerMatchStatistics,
)
from backend.app.db.models.modeling import (  # noqa: F401
    Backtest,
    ModelVersion,
    Prediction,
    PredictionResult,
)

__all__ = [
    "Competition",
    "Season",
    "Team",
    "TeamNameMapping",
    "Referee",
    "Player",
    "DataSource",
    "Match",
    "MatchStatistics",
    "MatchOdds",
    "PlayerMatchStatistics",
    "TeamMatchFeatures",
    "ModelVersion",
    "Prediction",
    "PredictionResult",
    "Backtest",
]
