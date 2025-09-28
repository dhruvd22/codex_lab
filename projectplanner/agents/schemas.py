"""Pydantic schemas exchanged between planner agents."""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from projectplanner.models import AgentReport, PromptPlan, PromptStep, TargetStack


class PlannerAgentInput(BaseModel):
    run_id: str
    chunks: List[str] = Field(..., description="Normalized document chunks to analyze.")
    target_stack: TargetStack
    style: str


class PlannerAgentOutput(BaseModel):
    plan: PromptPlan


class DecomposerAgentInput(BaseModel):
    run_id: str
    plan: PromptPlan
    target_stack: TargetStack


class DecomposerAgentOutput(BaseModel):
    steps: List[PromptStep]


class ReviewerAgentInput(BaseModel):
    run_id: str
    plan: PromptPlan
    steps: List[PromptStep]


class ReviewerAgentOutput(BaseModel):
    steps: List[PromptStep]
    report: AgentReport