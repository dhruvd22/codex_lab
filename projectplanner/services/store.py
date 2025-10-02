"""Persistence layer for the project planner module."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator, Iterable, List, Optional

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

from projectplanner.models import AgentReport, PromptPlan, PromptStep

Base = declarative_base()


class RunRecord(Base):
    __tablename__ = "runs"

    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source = Column(String, nullable=True)
    word_count = Column(Integer, nullable=False)
    char_count = Column(Integer, nullable=False)
    chunk_count = Column(Integer, nullable=False)
    target_stack = Column(JSON, nullable=True)
    style = Column(String, nullable=True)

    chunks = relationship("ChunkRecord", cascade="all, delete-orphan", back_populates="run")
    plan = relationship("PlanRecord", uselist=False, cascade="all, delete-orphan", back_populates="run")
    steps = relationship("StepRecord", cascade="all, delete-orphan", back_populates="run")
    report = relationship("ReportRecord", uselist=False, cascade="all, delete-orphan", back_populates="run")


class ChunkRecord(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    idx = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=True)

    run = relationship("RunRecord", back_populates="chunks")


class PlanRecord(Base):
    __tablename__ = "plans"

    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    plan_json = Column(JSON, nullable=False)

    run = relationship("RunRecord", back_populates="plan")


class StepRecord(Base):
    __tablename__ = "steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_index = Column(Integer, nullable=False)
    step_json = Column(JSON, nullable=False)

    run = relationship("RunRecord", back_populates="steps")


class ReportRecord(Base):
    __tablename__ = "reports"

    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    report_json = Column(JSON, nullable=False)

    run = relationship("RunRecord", back_populates="report")


@dataclass
class StoredChunk:
    """Lightweight representation of a document chunk."""

    idx: int
    text: str
    embedding: Optional[List[float]]
    metadata: Optional[dict]


class ProjectPlannerStore:
    """Data access layer supporting both SQLite and Postgres backends."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @classmethod
    def from_env(cls) -> "ProjectPlannerStore":
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            data_dir = Path(__file__).resolve().parent / "../data"
            data_dir = data_dir.resolve()
            data_dir.mkdir(parents=True, exist_ok=True)
            database_url = f"sqlite:///{data_dir / 'projectplanner.db'}"
        engine = create_engine(database_url, future=True, echo=False)
        return cls(engine)

    def ensure_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_run(self, run_id: str, *, source: str, stats: dict) -> None:
        with self.session() as session:
            session.merge(
                RunRecord(
                    id=run_id,
                    source=source,
                    word_count=stats["word_count"],
                    char_count=stats["char_count"],
                    chunk_count=stats["chunk_count"],
                )
            )

    def add_chunks(self, run_id: str, chunks: Iterable[StoredChunk]) -> None:
        with self.session() as session:
            for chunk in chunks:
                session.add(
                    ChunkRecord(
                        run_id=run_id,
                        idx=chunk.idx,
                        text=chunk.text,
                        embedding=json.dumps(chunk.embedding) if chunk.embedding else None,
                        metadata_json=chunk.metadata,
                    )
                )

    def get_chunks(self, run_id: str) -> List[StoredChunk]:
        with self.session() as session:
            records = (
                session.query(ChunkRecord)
                .filter(ChunkRecord.run_id == run_id)
                .order_by(ChunkRecord.idx)
                .all()
            )
        return [
            StoredChunk(
                idx=r.idx,
                text=r.text,
                embedding=json.loads(r.embedding) if r.embedding else None,
                metadata=r.metadata_json,
            )
            for r in records
        ]

    def upsert_plan(self, run_id: str, plan: PromptPlan) -> None:
        with self.session() as session:
            session.merge(PlanRecord(run_id=run_id, plan_json=plan.model_dump(mode="json")))

    def upsert_steps(self, run_id: str, steps: List[PromptStep]) -> None:
        with self.session() as session:
            session.query(StepRecord).filter(StepRecord.run_id == run_id).delete()
            for idx, step in enumerate(steps):
                session.add(
                    StepRecord(
                        run_id=run_id,
                        step_index=idx,
                        step_json=step.model_dump(mode="json"),
                    )
                )

    def upsert_report(self, run_id: str, report: AgentReport) -> None:
        with self.session() as session:
            session.merge(ReportRecord(run_id=run_id, report_json=report.model_dump(mode="json")))

    def attach_plan_context(self, run_id: str, *, target_stack: dict, style: str) -> None:
        with self.session() as session:
            record = session.get(RunRecord, run_id)
            if not record:
                return
            record.target_stack = target_stack
            record.style = style

    def get_plan(self, run_id: str) -> Optional[PromptPlan]:
        with self.session() as session:
            record = session.query(PlanRecord).filter(PlanRecord.run_id == run_id).one_or_none()
        if not record:
            return None
        return PromptPlan.parse_obj(record.plan_json)

    def get_steps(self, run_id: str) -> List[PromptStep]:
        with self.session() as session:
            records = (
                session.query(StepRecord)
                .filter(StepRecord.run_id == run_id)
                .order_by(StepRecord.step_index)
                .all()
            )
        return [PromptStep.parse_obj(record.step_json) for record in records]

    def get_report(self, run_id: str) -> Optional[AgentReport]:
        with self.session() as session:
            record = session.query(ReportRecord).filter(ReportRecord.run_id == run_id).one_or_none()
        if not record:
            return None
        return AgentReport.parse_obj(record.report_json)

    def run_exists(self, run_id: str) -> bool:
        with self.session() as session:
            return session.query(RunRecord.id).filter(RunRecord.id == run_id).scalar() is not None
