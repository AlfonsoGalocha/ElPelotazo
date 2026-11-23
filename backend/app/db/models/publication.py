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
