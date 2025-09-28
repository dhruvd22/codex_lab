"""Core Pydantic models for the project planner module."""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator


class IngestionRequest(BaseModel):
    """Request payload for ingesting a source document."""

    url: Optional[HttpUrl] = Field(None, description="Remote document to fetch and ingest.")
    text: Optional[str] = Field(
        None, min_length=1, description="Raw text content supplied by the caller."
    )
    file_id: Optional[str] = Field(
        None,
        description="Identifier for a previously uploaded file stored by the UI layer.",
    )
    format_hint: Optional[Literal["pdf", "md", "docx"]] = Field(
        None, description="Helps the ingestion pipeline pick the proper parser."
    )

    @model_validator(mode="after")
    def ensure_payload_present(cls, values: "IngestionRequest") -> "IngestionRequest":
        """Require at least one source of content to be provided."""

        if not (values.url or values.text or values.file_id):
            raise ValueError("Provide one of url, text, or file_id for ingestion.")
        return values


class DocumentStats(BaseModel):
    """Summary statistics for an ingested document."""

    word_count: int = Field(..., ge=0)
    char_count: int = Field(..., ge=0)
    chunk_count: int = Field(..., ge=0)


class IngestionResponse(BaseModel):
    """Response returned after ingesting a document."""

    run_id: str = Field(..., description="Unique identifier for the ingestion run.")
    stats: DocumentStats


class TargetStack(BaseModel):
    """Desired implementation stack for downstream planning."""

    backend: Literal["FastAPI"] = Field("FastAPI")
    frontend: Literal["Next.js"] = Field("Next.js")
    db: Literal["Postgres"] = Field("Postgres")


class PlanRequest(BaseModel):
    """Payload for triggering the planning workflow."""

    run_id: str = Field(..., description="Ingestion run to base the plan on.")
    target_stack: TargetStack = Field(default_factory=TargetStack)
    style: Literal["strict", "creative"] = Field("strict")


class PromptPlan(BaseModel):
    """High-level plan extracted from the research document."""

    context: str = Field(..., description="Concise domain context for the project.")
    goals: List[str] = Field(..., description="Primary objectives the build must satisfy.")
    assumptions: List[str] = Field(..., description="Key assumptions derived from the brief.")
    non_goals: List[str] = Field(..., description="Intentionally excluded scope items.")
    risks: List[str] = Field(..., description="Notable risks or open questions.")
    milestones: List[str] = Field(..., description="Sequenced high-level milestones.")


class PromptStep(BaseModel):
    """Executable build prompt for an AI coding agent."""

    id: str = Field(..., regex=r"^[a-z0-9\-]+$", description="Stable identifier for the step.")
    title: str
    system_prompt: str
    user_prompt: str
    expected_artifacts: List[str] = Field(..., min_items=1)
    tools: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(..., min_items=1)
    inputs: List[str] = Field(..., min_items=1)
    outputs: List[str] = Field(..., min_items=1)
    token_budget: int = Field(512, gt=0)
    cited_artifacts: List[str] = Field(default_factory=list)
    rubric_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Normalized quality score from the reviewer."
    )
    suggested_edits: Optional[str] = Field(
        None, description="Reviewer-suggested adjustments to clarify the step."
    )


class StepFeedback(BaseModel):
    """Structured reviewer feedback at the step level."""

    step_id: str
    rubric_score: float = Field(..., ge=0.0, le=1.0)
    notes: str


class AgentReport(BaseModel):
    """Reviewer agent summary of plan quality."""

    run_id: str
    generated_at: datetime
    overall_score: float = Field(..., ge=0.0, le=1.0)
    strengths: List[str]
    concerns: List[str]
    step_feedback: List[StepFeedback]


class PlanResponse(BaseModel):
    """Response envelope returned by the planning endpoint."""

    plan: PromptPlan
    steps: List[PromptStep]
    report: AgentReport


class StepsResponse(BaseModel):
    """Response envelope for retrieving stored steps."""

    run_id: str
    steps: List[PromptStep]


class ExportRequest(BaseModel):
    """Request body for exporting prompt artifacts."""

    run_id: str
    format: Literal["yaml", "jsonl", "md"]


class ExportMetadata(BaseModel):
    """Metadata provided alongside exported artifacts."""

    filename: str
    content_type: str
    generated_at: datetime


class ExportResponse(BaseModel):
    """Response returned when exporting prompts synchronously."""

    metadata: ExportMetadata
    content: str
class StepUpdateRequest(BaseModel):
    """Request payload for updating stored steps."""

    steps: List[PromptStep]
