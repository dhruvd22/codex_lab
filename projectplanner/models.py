"""Core Pydantic models for the coding conductor module."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class IngestionRequest(BaseModel):
    """Request payload for ingesting an application blueprint."""

    blueprint: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("blueprint", "text"),
        description=(
            "Base64-encoded blueprint file contents or inline architecture text prepared for synthesis."
        ),
    )
    filename: Optional[str] = Field(
        None,
        description="Original filename supplied by the caller for traceability in logs.",
    )
    format_hint: Optional[Literal["pdf", "md", "docx", "txt"]] = Field(
        None,
        description="Helps the ingestion pipeline pick the proper parser when the content type is ambiguous.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def ensure_blueprint_present(cls, values: "IngestionRequest") -> "IngestionRequest":
        """Ensure a blueprint payload is always provided."""

        blueprint = (values.blueprint or "").strip()
        if not blueprint:
            raise ValueError("Blueprint content must be provided for ingestion.")
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

class MilestoneObjective(BaseModel):
    """Ordered milestone objective produced by the coordinator agent."""

    id: str = Field(..., pattern=r"^[a-z0-9\-]+$", description="Stable identifier for the milestone.")
    order: int = Field(..., ge=0, description="Zero-based execution order.")
    title: str = Field(..., description="Concise milestone title.")
    objective: str = Field(..., description="Concrete outcome delivered by the milestone.")
    success_criteria: List[str] = Field(..., min_items=1, description="How we know the milestone is successful.")
    dependencies: List[str] = Field(default_factory=list, description="Milestone ids that must precede this milestone.")

class PromptPlan(BaseModel):
    """High-level application strategy extracted from the submitted blueprint."""

    context: str = Field(..., description="Concise domain context for the project.")
    goals: List[str] = Field(..., description="Primary objectives the build must satisfy.")
    assumptions: List[str] = Field(..., description="Key assumptions derived from the brief.")
    non_goals: List[str] = Field(..., description="Intentionally excluded scope items.")
    risks: List[str] = Field(..., description="Notable risks or open questions.")
    milestones: List[str] = Field(..., description="Sequenced high-level milestones.")


class PromptStep(BaseModel):
    """Execution-ready instructions empowering an autonomous AI to design and deliver a robust application."""

    id: str = Field(..., pattern=r"^[a-z0-9\-]+$", description="Stable identifier for the step.")
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
    objectives: List[MilestoneObjective] = Field(default_factory=list)


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

class LogEntry(BaseModel):
    """Structured log entry emitted by the logging manager."""

    sequence: int = Field(..., ge=1, description="Monotonic cursor for incremental retrieval.")
    timestamp: datetime = Field(..., description="UTC timestamp when the log was recorded.")
    level: str = Field(..., description="Severity level name.")
    logger: str = Field(..., description="Logger name that emitted the record.")
    message: str = Field(..., description="Primary log message.")
    type: Literal["runtime", "prompts"] = Field("runtime", description="Log stream category.")
    run_id: Optional[str] = Field(None, description="Associated run identifier when available.")
    event: Optional[str] = Field(None, description="Categorical event identifier supplied via extra context.")
    payload: Optional[Dict[str, Any]] = Field(None, description="Structured payload attached to the record.")
    exception: Optional[str] = Field(None, description="Rendered traceback when the record captured an exception.")


class LogsResponse(BaseModel):
    """Envelope returned when fetching captured logs."""

    logs: List[LogEntry] = Field(default_factory=list)
    cursor: int = Field(..., ge=0, description="Highest sequence id available on the server.")



class ObservabilityCall(BaseModel):
    """Recent invocation emitted by a module participating in the workflow."""

    module_id: str = Field(..., description="Identifier of the module that emitted the call.")
    timestamp: datetime = Field(..., description="Timestamp when the call occurred.")
    level: str = Field(..., description="Severity level associated with the call.")
    event: Optional[str] = Field(None, description="Event identifier recorded for the call.")
    message: str = Field(..., description="Primary log message for the call.")
    log_type: Literal["runtime", "prompts"] = Field("runtime", description="Log stream that produced the call.")
    run_id: Optional[str] = Field(None, description="Run identifier associated with the call, when available.")
    payload: Optional[Dict[str, Any]] = Field(None, description="Structured payload attached to the call.")


class ObservabilityNode(BaseModel):
    """Module surfaced on the observability dashboard."""

    id: str = Field(..., description="Stable identifier for the module.")
    name: str = Field(..., description="Human-friendly module name.")
    category: Literal["endpoint", "pipeline", "agent", "storage", "service", "orchestrator"] = Field(
        ..., description="Module category used for grouping and styling."
    )
    description: str = Field(..., description="Summary of what the module is responsible for.")
    status: Literal["idle", "healthy", "degraded", "error"] = Field(
        ..., description="Derived health status based on recent events."
    )
    event_count: int = Field(0, ge=0, description="Number of recent events observed for this module.")
    run_ids: List[str] = Field(default_factory=list, description="Recent run identifiers touching this module.")
    last_event: Optional[str] = Field(None, description="Most recent event associated with this module.")
    last_timestamp: Optional[datetime] = Field(None, description="Timestamp of the latest event.")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="Additional module-specific metrics.")


class ObservabilityEdge(BaseModel):
    """Directed relationship between workflow modules."""

    source: str = Field(..., description="Source module id.")
    target: str = Field(..., description="Target module id.")
    label: Optional[str] = Field(None, description="Optional label describing the edge relationship.")


class ObservabilityResponse(BaseModel):
    """Snapshot returned to populate the observability dashboard."""

    generated_at: datetime = Field(..., description="Timestamp when the snapshot was generated.")
    session_started_at: datetime = Field(..., description="Timestamp when the logging session began.")
    nodes: List[ObservabilityNode] = Field(..., description="Modules participating in the workflow.")
    edges: List[ObservabilityEdge] = Field(..., description="Directed relationships between modules.")
    calls: List[ObservabilityCall] = Field(default_factory=list, description="Recent module invocations used for drill-down.")

class StepUpdateRequest(BaseModel):
    """Request payload for updating stored steps."""

    steps: List[PromptStep]




