"""Integration tests for planning, plan validation, and replanning triggers."""

import pytest
from pydantic import ValidationError

from local_control.agent.budget import Budget
from local_control.agent.planner import Planner
from local_control.agent.runner import AgentRunner
from local_control.config.settings import Settings
from local_control.core.actions import ClickAction, DoneAction
from local_control.core.run_store import RunStore
from local_control.core.types import (
    Assessment,
    Plan,
    PlannerResponse,
    PlanStep,
    TaskState,
)
from local_control.models.fake import FakeModelProvider
from local_control.safety.approval import AutoApprovalGate
from tests.integration.fakes.fake_computer import FakeComputer


def test_plan_validation() -> None:
    # Valid plan
    plan = Plan(
        steps=[
            PlanStep(index=0, description="Step 1", status="active"),
            PlanStep(index=1, description="Step 2", status="pending"),
        ],
        current_index=0,
        revision=0,
    )
    assert plan.current_index == 0
    assert plan.revision == 0

    # Invalid current_index out of bounds
    with pytest.raises(ValidationError, match="current_index 2 is out of bounds"):
        Plan(
            steps=[
                PlanStep(index=0, description="Step 1"),
                PlanStep(index=1, description="Step 2"),
            ],
            current_index=2,
            revision=0,
        )


@pytest.mark.asyncio
async def test_replanning_scenario(tmp_path: any) -> None:
    """Test scenario where step 0 fails twice, triggering REPLAN REQUIRED and revision increment."""
    computer = FakeComputer()
    observer = computer.create_observer()
    executor = computer.create_executor()

    initial_plan = Plan(
        steps=[
            PlanStep(index=0, description="Click Primary Target", status="active"),
            PlanStep(index=1, description="Finish Task", status="pending"),
        ],
        current_index=0,
        revision=0,
    )

    revised_plan = Plan(
        steps=[
            PlanStep(index=0, description="Click Fallback Target", status="active"),
            PlanStep(index=1, description="Finish Task", status="pending"),
        ],
        current_index=0,
        revision=1,
    )

    # 1. Step 0 proposal (starts step 0)
    resp_0 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Initial screen",
            previous_action_outcome="not_applicable",
            evidence="Starting task",
        ),
        plan=initial_plan,
        action=ClickAction(
            x=50,
            y=50,
            target_description="Primary Target",
            expected_outcome="Click primary",
        ),
        confidence=0.9,
        rationale="Click target",
    )

    # 2. Step 1 proposal: reports failure #1 on step 0
    resp_1 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Screen unchanged",
            previous_action_outcome="failure",
            evidence="Target did not react to click",
        ),
        plan=initial_plan,
        action=ClickAction(
            x=50,
            y=50,
            target_description="Primary Target retry",
            expected_outcome="Retry click primary",
        ),
        confidence=0.7,
        rationale="Retry clicking target",
    )

    # 3. Step 2 proposal: reports failure #2 on step 0
    resp_2 = PlannerResponse(
        assessment=Assessment(
            screen_summary="Screen still unchanged",
            previous_action_outcome="failure",
            evidence="Target still not responding",
        ),
        plan=initial_plan,
        action=ClickAction(
            x=50,
            y=50,
            target_description="Primary Target retry 2",
            expected_outcome="Retry click again",
        ),
        confidence=0.5,
        rationale="Try clicking again",
    )

    # 4. Replan proposal: triggered by runner because step 0 failed twice consecutively!
    resp_replan = PlannerResponse(
        assessment=Assessment(
            screen_summary="Screen still unchanged, formulating fallback",
            previous_action_outcome="failure",
            evidence="Replan required after 2 failures",
        ),
        plan=revised_plan,
        action=ClickAction(
            x=200,
            y=200,
            target_description="Fallback Target",
            expected_outcome="Click fallback target",
        ),
        confidence=0.85,
        rationale="Using fallback button as replanned",
    )

    # 5. Final completion proposal
    resp_done = PlannerResponse(
        assessment=Assessment(
            screen_summary="Fallback succeeded",
            previous_action_outcome="success",
            evidence="State advanced to completed",
        ),
        plan=Plan(
            steps=[
                PlanStep(index=0, description="Click Fallback Target", status="done"),
                PlanStep(index=1, description="Finish Task", status="done"),
            ],
            current_index=1,
            revision=1,
        ),
        action=DoneAction(
            summary="Goal accomplished via fallback",
            verification_notes="Target reached",
            target_description="Done marker",
            expected_outcome="Complete run",
        ),
        confidence=1.0,
        rationale="Task done",
    )

    provider = FakeModelProvider(
        scripted_responses=[
            resp_0.model_dump(),
            resp_1.model_dump(),
            resp_2.model_dump(),
            resp_replan.model_dump(),
            resp_done.model_dump(),
        ]
    )

    planner = Planner(provider=provider)
    run_store = RunStore(base_dir=tmp_path / "runs")
    settings = Settings(run_store_dir=tmp_path / "runs")
    budget = Budget(settings=settings)

    runner = AgentRunner(
        planner=planner,
        executor=executor,
        observer=observer,
        approval_gate=AutoApprovalGate(),
        run_store=run_store,
        budget=budget,
        settings=settings,
    )

    result = await runner.run(goal="Test replanning", autonomy_mode="assisted")

    assert result.status == "COMPLETED"
    # Check that REPLAN REQUIRED was in one of the captured requests
    replan_requests = [
        req
        for req in provider.requests
        if any("REPLAN REQUIRED" in p.text for p in req.messages[0].parts if hasattr(p, "text"))
    ]
    assert len(replan_requests) >= 1
    # Check that the summary contains the updated plan revision
    assert "**Revision**: 1" in result.summary


@pytest.mark.asyncio
async def test_replan_revision_rejection() -> None:
    """Test that a replan request rejects a response with identical or lower revision."""
    initial_plan = Plan(
        steps=[PlanStep(index=0, description="Step 1")],
        current_index=0,
        revision=1,
    )
    state = TaskState(
        run_id="test-run",
        goal="Test revision check",
        autonomy_mode="step",
        plan=initial_plan,
    )

    # Bad response with same revision=1
    bad_resp = PlannerResponse(
        assessment=Assessment(
            screen_summary="Screen",
            previous_action_outcome="failure",
            evidence="Failed",
        ),
        plan=Plan(
            steps=[PlanStep(index=0, description="Step 1")],
            current_index=0,
            revision=1,
        ),
        action=ClickAction(
            x=10,
            y=10,
            target_description="Retry",
            expected_outcome="Retry",
        ),
        confidence=0.5,
        rationale="Retrying",
    )

    # Good response with revision=2
    good_resp = PlannerResponse(
        assessment=Assessment(
            screen_summary="Screen",
            previous_action_outcome="failure",
            evidence="Failed",
        ),
        plan=Plan(
            steps=[PlanStep(index=0, description="New Step")],
            current_index=0,
            revision=2,
        ),
        action=ClickAction(
            x=20,
            y=20,
            target_description="New Plan Action",
            expected_outcome="Work",
        ),
        confidence=0.9,
        rationale="New plan",
    )

    provider = FakeModelProvider(
        scripted_responses=[
            bad_resp.model_dump(),
            good_resp.model_dump(),
        ]
    )

    planner = Planner(provider=provider)
    obs = FakeComputer().create_observer().observe(step_index=0)

    plan_resp = await planner.propose(state=state, obs=obs, replan_reason="Step failed twice")
    assert plan_resp.plan is not None
    assert plan_resp.plan.revision == 2
    # Verify two attempts were made (1 retry)
    assert len(provider.requests) == 2
