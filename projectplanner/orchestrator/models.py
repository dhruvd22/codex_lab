"""Pydantic models for The Coding Orchestrator workflow."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class BlueprintSummary(BaseModel):
    """Normalized summary returned after processing a blueprint."""

    run_id: str = Field(..., description="Identifier for the orchestrator run.")
    summary: str = Field(..., description="Narrative summary of the desired application.")
    highlights: List[str] = Field(default_factory=list, description="Key takeaways extracted from the blueprint.")
    risks: List[str] = Field(default_factory=list, description="Known risks or open questions.")
    components: List[str] = Field(default_factory=list, description="Major components captured for the graph store.")
    metadata: dict = Field(default_factory=dict, description="Additional structured metadata extracted from GPT.")


class Milestone(BaseModel):
    """Milestone representation produced by GPT."""

    milestone_id: int = Field(..., ge=1, description="Ordinal milestone identifier (1-based).")
    details: str = Field(..., description="Descriptive milestone narrative.")
    context: str = Field(default="", description="Context drawn from the blueprint to support the milestone.")


class MilestonePlan(BaseModel):
    """Container for milestone outputs."""

    run_id: str
    milestones: List[Milestone] = Field(default_factory=list)
    raw_response: Optional[str] = Field(None, description="Raw JSON payload returned by GPT before parsing.")


class MilestonePrompt(BaseModel):
    """Prompt specification usable by downstream coding agents."""

    milestone_id: int
    title: str
    system_prompt: str
    user_prompt: str
    acceptance_criteria: List[str] = Field(default_factory=list)
    expected_artifacts: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)


class PromptBundle(BaseModel):
    """Sequential prompts derived for each milestone."""

    run_id: str
    prompts: List[MilestonePrompt] = Field(default_factory=list)


class GraphNode(BaseModel):
    """Represents a component tracked within the graph store."""

    id: str
    name: str
    description: Optional[str] = None
    milestone_ids: List[int] = Field(default_factory=list)


class GraphCoverageSnapshot(BaseModel):
    """Coverage details produced by the graph audit step."""

    run_id: str
    covered_nodes: List[str] = Field(default_factory=list)
    uncovered_nodes: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class OrchestratorResult(BaseModel):
    """Final aggregated response produced by the orchestrator."""

    run_id: str
    summary: BlueprintSummary
    milestones: MilestonePlan
    prompts: PromptBundle
    graph_report: GraphCoverageSnapshot
    generated_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "BlueprintSummary",
    "Milestone",
    "MilestonePlan",
    "MilestonePrompt",
    "PromptBundle",
    "GraphNode",
    "GraphCoverageSnapshot",
    "OrchestratorResult",
]
