"""Persistence layer for the coding conductor module."""
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

from projectplanner.models import AgentReport, MilestoneObjective, PromptPlan, PromptStep
from projectplanner.logging_utils import get_logger

Base = declarative_base()

LOGGER = get_logger(__name__)


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
    milestones = relationship("MilestoneRecord", cascade="all, delete-orphan", back_populates="run")
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

class MilestoneRecord(Base):
    __tablename__ = "milestones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    milestone_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    objective = Column(Text, nullable=False)
    success_criteria = Column(JSON, nullable=False)
    dependencies = Column(JSON, nullable=False)
    display_order = Column(Integer, nullable=False)

    run = relationship("RunRecord", back_populates="milestones")



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
            database_url = f"sqlite:///{data_dir / 'codingconductor.db'}"
        engine = create_engine(database_url, future=True, echo=False)
        return cls(engine)

    def ensure_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        try:
            safe_url = self.engine.url.render_as_string(hide_password=True)
        except Exception:  # pragma: no cover - defensive
            safe_url = str(self.engine.url)
        LOGGER.debug(
            "Ensured coding conductor schema on %s",
            safe_url,
            extra={"event": "store.schema.ensure"},
        )

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
        LOGGER.info(
            "Registered ingestion run %s",
            run_id,
            extra={"event": "store.run.register", "run_id": run_id, "payload": {"source": source}},
        )

    def add_chunks(self, run_id: str, chunks: Iterable[StoredChunk]) -> None:
        count = 0
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
                count += 1
        LOGGER.debug(
            "Persisted %s chunks for run %s",
            count,
            run_id,
            extra={"event": "store.chunks.add", "run_id": run_id, "payload": {"count": count}},
        )

    def get_chunks(self, run_id: str) -> List[StoredChunk]:
        with self.session() as session:
            records = (
                session.query(ChunkRecord)
                .filter(ChunkRecord.run_id == run_id)
                .order_by(ChunkRecord.idx)
                .all()
            )
        chunks = [
            StoredChunk(
                idx=r.idx,
                text=r.text,
                embedding=json.loads(r.embedding) if r.embedding else None,
                metadata=r.metadata_json,
            )
            for r in records
        ]
        LOGGER.debug(
            "Retrieved %s chunk records for run %s",
            len(chunks),
            run_id,
            extra={"event": "store.chunks.fetch", "run_id": run_id, "payload": {"count": len(chunks)}},
        )
        return chunks

    def upsert_plan(self, run_id: str, plan: PromptPlan) -> None:
        with self.session() as session:
            session.merge(PlanRecord(run_id=run_id, plan_json=plan.model_dump(mode="json")))
        LOGGER.info(
            "Upserted plan for run %s",
            run_id,
            extra={"event": "store.plan.upsert", "run_id": run_id},
        )


    def upsert_objectives(self, run_id: str, objectives: List[MilestoneObjective]) -> None:
        with self.session() as session:
            session.query(MilestoneRecord).filter(MilestoneRecord.run_id == run_id).delete()
            for objective in sorted(objectives, key=lambda item: item.order):
                session.add(
                    MilestoneRecord(
                        run_id=run_id,
                        milestone_id=objective.id,
                        title=objective.title,
                        objective=objective.objective,
                        success_criteria=list(objective.success_criteria),
                        dependencies=list(objective.dependencies),
                        display_order=objective.order,
                    )
                )
        LOGGER.info(
            "Upserted %s objectives for run %s",
            len(objectives),
            run_id,
            extra={"event": "store.objectives.upsert", "run_id": run_id, "payload": {"count": len(objectives)}},
        )

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
        LOGGER.info(
            "Upserted %s steps for run %s",
            len(steps),
            run_id,
            extra={"event": "store.steps.upsert", "run_id": run_id, "payload": {"count": len(steps)}},
        )

    def upsert_report(self, run_id: str, report: AgentReport) -> None:
        with self.session() as session:
            session.merge(ReportRecord(run_id=run_id, report_json=report.model_dump(mode="json")))
        LOGGER.info(
            "Upserted reviewer report for run %s",
            run_id,
            extra={"event": "store.report.upsert", "run_id": run_id, "payload": {"overall_score": report.overall_score}},
        )

    def attach_plan_context(self, run_id: str, *, target_stack: dict, style: str) -> None:
        updated = False
        with self.session() as session:
            record = session.get(RunRecord, run_id)
            if not record:
                return
            record.target_stack = target_stack
            record.style = style
            updated = True
        if updated:
            LOGGER.debug(
                "Attached plan context for run %s",
                run_id,
                extra={"event": "store.run.context", "run_id": run_id, "payload": {"style": style}},
            )

    def get_plan(self, run_id: str) -> Optional[PromptPlan]:
        with self.session() as session:
            record = session.query(PlanRecord).filter(PlanRecord.run_id == run_id).one_or_none()
        if not record:
            LOGGER.debug(
                "No plan record found for run %s",
                run_id,
                extra={"event": "store.plan.fetch", "run_id": run_id, "payload": {"found": False}},
            )
            return None
        LOGGER.debug(
            "Loaded plan record for run %s",
            run_id,
            extra={"event": "store.plan.fetch", "run_id": run_id, "payload": {"found": True}},
        )
        return PromptPlan.parse_obj(record.plan_json)


    def get_objectives(self, run_id: str) -> List[MilestoneObjective]:
        with self.session() as session:
            records = (
                session.query(MilestoneRecord)
                .filter(MilestoneRecord.run_id == run_id)
                .order_by(MilestoneRecord.display_order)
                .all()
            )
        objectives = [
            MilestoneObjective(
                id=record.milestone_id,
                order=record.display_order,
                title=record.title,
                objective=record.objective,
                success_criteria=list(record.success_criteria or []),
                dependencies=list(record.dependencies or []),
            )
            for record in records
        ]
        LOGGER.debug(
            "Retrieved %s objectives for run %s",
            len(objectives),
            run_id,
            extra={"event": "store.objectives.fetch", "run_id": run_id, "payload": {"count": len(objectives)}},
        )
        return objectives

    def get_steps(self, run_id: str) -> List[PromptStep]:
        with self.session() as session:
            records = (
                session.query(StepRecord)
                .filter(StepRecord.run_id == run_id)
                .order_by(StepRecord.step_index)
                .all()
            )
        steps = [PromptStep.parse_obj(record.step_json) for record in records]
        LOGGER.debug(
            "Retrieved %s steps for run %s",
            len(steps),
            run_id,
            extra={"event": "store.steps.fetch", "run_id": run_id, "payload": {"count": len(steps)}},
        )
        return steps

    def get_report(self, run_id: str) -> Optional[AgentReport]:
        with self.session() as session:
            record = session.query(ReportRecord).filter(ReportRecord.run_id == run_id).one_or_none()
        if not record:
            LOGGER.debug(
                "No report found for run %s",
                run_id,
                extra={"event": "store.report.fetch", "run_id": run_id, "payload": {"found": False}},
            )
            return None
        LOGGER.debug(
            "Loaded report for run %s",
            run_id,
            extra={"event": "store.report.fetch", "run_id": run_id, "payload": {"found": True}},
        )
        return AgentReport.parse_obj(record.report_json)

    def run_exists(self, run_id: str) -> bool:
        with self.session() as session:
            exists = session.query(RunRecord.id).filter(RunRecord.id == run_id).scalar() is not None
        LOGGER.debug(
            "Run %s existence check returned %s",
            run_id,
            exists,
            extra={"event": "store.run.exists", "run_id": run_id, "payload": {"exists": exists}},
        )
        return exists
