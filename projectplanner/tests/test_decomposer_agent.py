import json
from types import SimpleNamespace

import pytest

from projectplanner.agents.decomposer_agent import DecomposerAgent
from projectplanner.agents.schemas import DecomposerAgentInput
from projectplanner.models import MilestoneObjective, PromptPlan, TargetStack


def make_completion_response(content: str, finish_reason: str | None = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        id="resp-test",
        model="gpt-test",
    )


def build_step_payload(seed: str) -> str:
    return json.dumps(
        {
            "system_prompt": f"sys-{seed}",
            "user_prompt": f"user-{seed}",
            "expected_artifacts": [f"artifact-{seed}"],
            "tools": ["git"],
            "acceptance_criteria": [f"criteria-{seed}"],
            "inputs": ["ingested_research"],
            "outputs": [f"output-{seed}"],
            "token_budget": 700,
        }
    )


def make_payload(milestones: list[str]) -> DecomposerAgentInput:
    plan = PromptPlan(
        context="Context",
        goals=["goal"],
        assumptions=["assumption"],
        non_goals=["non-goal"],
        risks=["risk"],
        milestones=milestones,
    )
    objectives = [
        MilestoneObjective(
            id=f"m{i:02d}",
            order=i,
            title=title,
            objective=f"Objective {i}",
            success_criteria=[f"Criteria {i}"],
            dependencies=[],
        )
        for i, title in enumerate(milestones)
    ]
    return DecomposerAgentInput(
        run_id="run-1",
        plan=plan,
        target_stack=TargetStack(),
        objectives=objectives,
    )


def test_decomposer_retries_on_length(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        make_completion_response("", finish_reason="length"),
        make_completion_response(build_step_payload("00")),
    ]
    responses_iter = iter(responses)
    calls: list[dict[str, object]] = []

    def fake_completion(client, *, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls.append({"messages": messages, "max_tokens": max_tokens})
        try:
            return next(responses_iter)
        except StopIteration as exc:
            raise AssertionError("Unexpected extra completion call") from exc

    monkeypatch.setattr("projectplanner.agents.decomposer_agent.create_chat_completion", fake_completion)
    monkeypatch.setattr("projectplanner.agents.decomposer_agent.log_prompt", lambda *args, **kwargs: None)

    agent = DecomposerAgent()
    agent._client = object()
    agent._model = "test-model"

    payload = make_payload(["Milestone"])
    result = agent.decompose(payload)

    assert len(result.steps) == 1
    assert len(calls) == 2
    assert calls[1]["max_tokens"] >= calls[0]["max_tokens"]
    step = result.steps[0]
    assert step.system_prompt == "sys-00"
    assert step.user_prompt == "user-00"
    assert step.expected_artifacts == ["artifact-00"]


def test_decomposer_retry_trims_previous_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    milestones = [f"Milestone {idx + 1}" for idx in range(5)]
    responses = [
        make_completion_response(build_step_payload("00")),
        make_completion_response(build_step_payload("01")),
        make_completion_response(build_step_payload("02")),
        make_completion_response(build_step_payload("03")),
        make_completion_response("", finish_reason="length"),
        make_completion_response(build_step_payload("04")),
    ]
    responses_iter = iter(responses)
    calls: list[dict[str, object]] = []

    def fake_completion(client, *, model, messages, temperature, max_tokens):  # noqa: ANN001
        calls.append({"messages": messages, "max_tokens": max_tokens})
        try:
            return next(responses_iter)
        except StopIteration as exc:
            raise AssertionError("Unexpected extra completion call") from exc

    monkeypatch.setattr("projectplanner.agents.decomposer_agent.create_chat_completion", fake_completion)
    monkeypatch.setattr("projectplanner.agents.decomposer_agent.log_prompt", lambda *args, **kwargs: None)

    agent = DecomposerAgent()
    agent._client = object()
    agent._model = "test-model"

    payload = make_payload(milestones)
    result = agent.decompose(payload)

    assert len(result.steps) == 5
    assert len(calls) == len(responses)

    first_attempt_prompt = calls[-2]["messages"][1]["content"]  # type: ignore[index]
    second_attempt_prompt = calls[-1]["messages"][1]["content"]  # type: ignore[index]

    def extract_statuses(prompt: str) -> list[str]:
        for line in prompt.splitlines():
            if line.startswith("Prior milestone status: "):
                return json.loads(line.split(": ", 1)[1])
        raise AssertionError("Prior milestone status line missing")

    first_statuses = extract_statuses(first_attempt_prompt)
    second_statuses = extract_statuses(second_attempt_prompt)

    assert len(first_statuses) == 4  # all prior steps included on the first attempt
    assert len(second_statuses) == 3  # trimmed list on retry
