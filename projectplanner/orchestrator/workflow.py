"""Workflow orchestration for The Coding Orchestrator."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Tuple

from projectplanner.logging_utils import get_logger
from projectplanner.orchestrator.agents import AgentPlanner, GraphAuditAgent, MilestonesAgent
from projectplanner.orchestrator.graph_store import GraphStore
from projectplanner.orchestrator.models import (
    BlueprintSummary,
    GraphCoverageSnapshot,
    MilestonePlan,
    OrchestratorResult,
    PromptBundle,
)

LOGGER = get_logger(__name__)

MAX_CANDIDATE_PATH_CHARS = 512


class CodingOrchestrator:
    """Coordinates the end-to-end milestone and prompt workflow."""

    def __init__(
        self,
        *,
        run_id: Optional[str] = None,
        milestones_agent: Optional[MilestonesAgent] = None,
        agent_planner: Optional[AgentPlanner] = None,
        graph_audit_agent: Optional[GraphAuditAgent] = None,
    ) -> None:
        self.run_id = run_id or f"orch-{uuid.uuid4()}"
        self._milestones_agent = milestones_agent or MilestonesAgent()
        self._agent_planner = agent_planner or AgentPlanner()
        self._graph_audit_agent = graph_audit_agent or GraphAuditAgent()
        self._graph_store = GraphStore(self.run_id)
        self._blueprint_text: Optional[str] = None
        self._summary: Optional[BlueprintSummary] = None
        self._summary_approved = False
        self._milestones: Optional[MilestonePlan] = None
        self._milestones_approved = False
        self._graph_snapshot: Optional[GraphCoverageSnapshot] = None
        self._prompts: Optional[PromptBundle] = None

    def ingest_blueprint(self, blueprint_source: str | Path) -> BlueprintSummary:
        """Ingest a blueprint file or inline text and synthesize the summary."""

        text = self._read_blueprint(blueprint_source)
        self._blueprint_text = text
        LOGGER.info(
            "Blueprint ingested",
            extra={"event": "orchestrator.ingest.complete", "run_id": self.run_id},
        )
        LOGGER.info(
            "Summary synthesis starting",
            extra={"event": "orchestrator.summary.start", "run_id": self.run_id},
        )
        summary = self._milestones_agent.summarize_blueprint(run_id=self.run_id, blueprint_text=text)
        self._summary = summary
        self._summary_approved = False
        self._milestones = None
        self._milestones_approved = False
        self._prompts = None
        self._graph_snapshot = None
        self._graph_store = GraphStore(self.run_id)
        self._graph_store.load_components(summary.components)
        LOGGER.info(
            "Summary ready for review",
            extra={
                "event": "orchestrator.summary.prepared",
                "run_id": self.run_id,
                "payload": {"highlight_count": len(summary.highlights), "component_count": len(summary.components)},
            },
        )
        return summary

    def approve_summary(self, approved: bool) -> None:
        """Persist the caller's decision on the generated summary."""

        if not self._summary:
            raise RuntimeError("No summary available to approve. Run ingest_blueprint first.")
        self._summary_approved = approved
        LOGGER.info(
            "Summary approval updated",
            extra={
                "event": "orchestrator.summary.approval",
                "run_id": self.run_id,
                "payload": {"approved": approved},
            },
        )

    def generate_milestones(self) -> Tuple[MilestonePlan, GraphCoverageSnapshot]:
        """Generate milestones after summary approval and run initial graph audit."""

        if not self._summary:
            raise RuntimeError("Summary not generated. Call ingest_blueprint first.")
        if not self._summary_approved:
            raise RuntimeError("Summary must be approved before generating milestones.")

        LOGGER.info(
            "Milestone synthesis starting",
            extra={"event": "orchestrator.milestones.start", "run_id": self.run_id},
        )
        plan = self._milestones_agent.generate_milestones(run_id=self.run_id, summary=self._summary)
        self._milestones = plan
        self._graph_store.assign_milestones(plan.milestones)
        snapshot = self._graph_audit_agent.audit(
            run_id=self.run_id,
            summary=self._summary,
            milestones=plan.milestones,
            graph_store=self._graph_store,
        )
        self._graph_snapshot = snapshot
        LOGGER.info(
            "Milestones ready for approval",
            extra={
                "event": "orchestrator.milestones.prepared",
                "run_id": self.run_id,
                "payload": {"milestone_count": len(plan.milestones)},
            },
        )
        return plan, snapshot

    def approve_milestones(self, approved: bool) -> None:
        """Record the decision to continue beyond the milestone stage."""

        if not self._milestones:
            raise RuntimeError("Milestones not generated yet.")
        self._milestones_approved = approved
        LOGGER.info(
            "Milestones approval updated",
            extra={
                "event": "orchestrator.milestones.approval",
                "run_id": self.run_id,
                "payload": {"approved": approved},
            },
        )

    def generate_prompts(self) -> PromptBundle:
        """Generate milestone prompts once approvals are in place."""

        if not self._summary:
            raise RuntimeError("Summary not generated. Call ingest_blueprint first.")
        if not self._milestones:
            raise RuntimeError("Milestones not generated. Call generate_milestones first.")
        if not self._milestones_approved:
            raise RuntimeError("Milestones must be approved before prompt generation.")

        LOGGER.info(
            "Prompt planning starting",
            extra={"event": "orchestrator.prompts.start", "run_id": self.run_id},
        )
        snapshot = self._graph_snapshot or self._graph_store.snapshot()
        prompts = self._agent_planner.generate_prompts(
            run_id=self.run_id,
            summary=self._summary,
            milestones=self._milestones.milestones,
            graph_snapshot=snapshot,
        )
        self._prompts = prompts
        LOGGER.info(
            "Prompts generated for orchestration",
            extra={
                "event": "orchestrator.prompts.generated",
                "run_id": self.run_id,
                "payload": {"prompt_count": len(prompts.prompts)},
            },
        )
        return prompts

    def finalize(self) -> OrchestratorResult:
        """Return the aggregated result for downstream consumers."""

        if not (self._summary and self._milestones and self._prompts):
            raise RuntimeError("Workflow incomplete. Ensure prompts are generated before finalizing.")
        snapshot = self._graph_snapshot or self._graph_store.snapshot()
        result = OrchestratorResult(
            run_id=self.run_id,
            summary=self._summary,
            milestones=self._milestones,
            prompts=self._prompts,
            graph_report=snapshot,
        )
        LOGGER.info(
            "Orchestration finalized",
            extra={"event": "orchestrator.workflow.finalized", "run_id": self.run_id},
        )
        return result

    def regenerate_summary(self) -> BlueprintSummary:
        """Regenerate the blueprint summary using the stored blueprint text."""

        if not self._blueprint_text:
            raise RuntimeError("No blueprint available to regenerate summary.")
        return self.ingest_blueprint(self._blueprint_text)

    def get_summary(self) -> Optional[BlueprintSummary]:
        return self._summary

    def get_milestone_plan(self) -> Optional[MilestonePlan]:
        return self._milestones

    def get_graph_snapshot(self) -> Optional[GraphCoverageSnapshot]:
        return self._graph_snapshot

    def current_graph_snapshot(self) -> GraphCoverageSnapshot:
        if self._graph_snapshot is not None:
            return self._graph_snapshot
        return self._graph_store.snapshot()

    def get_prompts(self) -> Optional[PromptBundle]:
        return self._prompts

    @property
    def summary_ready(self) -> bool:
        return self._summary is not None

    @property
    def summary_approved(self) -> bool:
        return self._summary_approved

    @property
    def milestones_ready(self) -> bool:
        return self._milestones is not None

    @property
    def milestones_approved(self) -> bool:
        return self._milestones_approved

    @property
    def prompts_ready(self) -> bool:
        return self._prompts is not None

    @staticmethod
    def _read_blueprint(source: str | Path) -> str:
        if isinstance(source, Path):
            try:
                if source.exists():
                    text = source.read_text(encoding="utf-8")
                    if not text.strip():
                        raise ValueError(f"Blueprint file {source} is empty.")
                    return text
            except OSError as exc:
                raise ValueError(f"Unable to read blueprint file {source}: {exc}") from exc
            raise FileNotFoundError(f"Blueprint file not found: {source}")

        if isinstance(source, str):
            inline_text = source
            stripped = inline_text.strip()
            if not stripped:
                raise ValueError("Blueprint text cannot be empty.")

            looks_like_path = (
                len(stripped) <= MAX_CANDIDATE_PATH_CHARS
                and "\n" not in inline_text
                and "\r" not in inline_text
            )
            if looks_like_path:
                try:
                    candidate_path = Path(stripped)
                except (TypeError, ValueError, OSError):
                    candidate_path = None
                else:
                    try:
                        if candidate_path.exists():
                            text = candidate_path.read_text(encoding="utf-8")
                            if not text.strip():
                                raise ValueError(f"Blueprint file {candidate_path} is empty.")
                            return text
                    except OSError:
                        candidate_path = None
            return inline_text

        text = str(source)
        if not text.strip():
            raise ValueError("Blueprint text cannot be empty.")
        return text


__all__ = ["CodingOrchestrator"]
