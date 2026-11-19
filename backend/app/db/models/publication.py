"""Immutable pre-match output of the official fixture capture."""

from sqlalchemy import JSON, DateTime, ForeignKey, String
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
