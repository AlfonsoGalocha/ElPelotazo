"""Immutable pre-match output of the official fixture capture."""

from datetime import date

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base


class FixturePublication(Base):
    __tablename__ = "fixture_publications"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, nullable=False)
    scheduled_kickoff = mapped_column(DateTime, nullable=False)
    ranking_version: Mapped[str] = mapped_column(String(32))
    ranking: Mapped[list] = mapped_column(JSON)
    groups: Mapped[dict] = mapped_column(JSON)
    recommendation: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class FixtureRankingRevision(Base):
    __tablename__ = "fixture_ranking_revisions"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, primary_key=True)
    scheduled_kickoff = mapped_column(DateTime, nullable=False)
    ranking_version: Mapped[str] = mapped_column(String(32))
    ranking: Mapped[list] = mapped_column(JSON)


class BotDecision(Base):
    """One immutable forward decision per Madrid date, including days with no tickets."""

    __tablename__ = "bot_decisions"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class BotTrialDecision(Base):
    """Standalone forward test with its own virtual opening balance."""

    __tablename__ = "bot_trial_decisions"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class BotTrialCut(Base):
    """Append-only batches for the continuing virtual trial bank."""

    __tablename__ = "bot_trial_cuts"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class BotManualTicket(Base):
    """Manual virtual ticket kept separate from the automatic pre-match cut."""

    __tablename__ = "bot_manual_tickets"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class StrategyCapture(Base):
    """Append-only comparison, independent of official decisions and betting policies."""

    __tablename__ = "strategy_captures"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class StrategyScan(Base):
    """Durable observations of automatic laboratory scans."""

    __tablename__ = "strategy_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at = mapped_column(DateTime, nullable=False, index=True)
    finished_at = mapped_column(DateTime, nullable=False)
    ok: Mapped[bool]
    captures: Mapped[int | None]
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ModelComparisonCut(Base):
    """Immutable paired forecasts, separate from official and strategy decisions."""

    __tablename__ = "model_comparison_cuts"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class PreMatchForecast(Base):
    """Last forecast shown before kickoff (main-page preview), kept after the match ends."""

    __tablename__ = "pre_match_forecasts"

    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    captured_at = mapped_column(DateTime, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
