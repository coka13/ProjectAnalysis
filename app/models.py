"""SQLAlchemy ORM models for local storage.

This is a single-user desktop application: there are no accounts, workspaces,
memberships or permissions. Everything below is plain local project data stored
in a SQLite file inside the application data directory.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uid() -> str:
    return uuid.uuid4().hex


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class SourceKind(str, enum.Enum):
    LOCAL = "local"
    GIT = "git"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalState(str, enum.Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind), default=SourceKind.LOCAL)
    source_location: Mapped[str] = mapped_column(Text, nullable=False)
    default_ref: Mapped[str] = mapped_column(String(200), default="")
    include_globs: Mapped[list] = mapped_column(JSON, default=list)
    exclude_globs: Mapped[list] = mapped_column(JSON, default=list)

    analyses: Mapped[list["AnalysisRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class AnalysisRun(Base, TimestampMixin):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(200), default="")
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(120), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    graph_path: Mapped[str] = mapped_column(Text, default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="analyses")
    diagrams: Mapped[list["Diagram"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")


class Diagram(Base, TimestampMixin):
    __tablename__ = "diagrams"
    __table_args__ = (Index("ix_diagram_analysis_kind", "analysis_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(240), default="")
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    mermaid: Mapped[str] = mapped_column(Text, default="")
    plantuml: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)  # {"en": {...}, "he": {...}}
    approval_state: Mapped[ApprovalState] = mapped_column(Enum(ApprovalState), default=ApprovalState.DRAFT)
    version: Mapped[int] = mapped_column(Integer, default=1)

    analysis: Mapped[AnalysisRun] = relationship(back_populates="diagrams")
    versions: Mapped[list["DiagramVersion"]] = relationship(back_populates="diagram", cascade="all, delete-orphan")
    comments: Mapped[list["Comment"]] = relationship(back_populates="diagram", cascade="all, delete-orphan")


class DiagramVersion(Base, TimestampMixin):
    __tablename__ = "diagram_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    diagram_id: Mapped[str] = mapped_column(ForeignKey("diagrams.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    mermaid: Mapped[str] = mapped_column(Text, default="")
    plantuml: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")

    diagram: Mapped[Diagram] = relationship(back_populates="versions")


class Comment(Base, TimestampMixin):
    """Local annotations pinned to a diagram - a personal review checklist."""

    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    diagram_id: Mapped[str] = mapped_column(ForeignKey("diagrams.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    anchor: Mapped[str] = mapped_column(String(240), default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    diagram: Mapped[Diagram] = relationship(back_populates="comments")


class ProviderConfig(Base, TimestampMixin):
    """The single locally configured AI provider."""

    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(120), default="default")
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff_seconds: Mapped[float] = mapped_column(Float, default=1.5)
    streaming: Mapped[bool] = mapped_column(Boolean, default=True)
