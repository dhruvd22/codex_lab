"""Pydantic schemas exchanged between planner agents."""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from projectplanner.models import AgentReport, MilestoneObjective, PromptPlan, PromptStep, TargetStack


class CoordinatorAgentInput(BaseModel):
    run_id: str
    chunks: List[str] = Field(..., description="Normalized document chunks to analyze.")
    target_stack: TargetStack
    style: str


class CoordinatorAgentOutput(BaseModel):
    objectives: List[MilestoneObjective]


class PlannerAgentInput(BaseModel):
    run_id: str
    chunks: List[str] = Field(..., description="Normalized document chunks to analyze.")
    target_stack: TargetStack
    style: str
    objectives: List[MilestoneObjective] = Field(default_factory=list)


class PlannerAgentOutput(BaseModel):
    plan: PromptPlan


class DecomposerAgentInput(BaseModel):
    run_id: str
    plan: PromptPlan
    target_stack: TargetStack
    objectives: List[MilestoneObjective] = Field(default_factory=list)


class DecomposerAgentOutput(BaseModel):
    steps: List[PromptStep]


class ReviewerAgentInput(BaseModel):
    run_id: str
    plan: PromptPlan
    steps: List[PromptStep]


class ReviewerAgentOutput(BaseModel):
    steps: List[PromptStep]
    report: AgentReport