"""Autonomous agent runner driving the observe-reason-propose-validate-execute loop."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from local_control.agent.budget import Budget
from local_control.agent.planner import Planner
from local_control.config.settings import Settings
from local_control.core.actions import DoneAction, FailAction
from local_control.core.coordinates import CoordinateMapper
from local_control.core.events import Event, EventBus
from local_control.core.run_store import RunStore
from local_control.core.types import (
    ActionResult,
    ApprovalDecision,
    Observation,
    RunStatus,
    StepRecord,
    TaskState,
    Verdict,
)
from local_control.execution.executor import Executor
from local_control.execution.tools.base import ExecutionContext
from local_control.observation.observer import Observer
from local_control.safety.approval import ApprovalGate, CliApprovalGate
from local_control.safety.kill_switch import KillSwitch, StopToken

logger = structlog.get_logger(__name__)


@dataclass
class RunResult:
    """Outcome and artifacts of a completed or stopped agent run."""

    run_id: str
    status: RunStatus
    steps_count: int
    goal: str
    summary: str


class AgentRunner:
    """Drives the observe -> propose -> validate -> approve -> execute loop."""

    def __init__(
        self,
        planner: Planner,
        executor: Executor,
        observer: Observer,
        approval_gate: ApprovalGate | None = None,
        run_store: RunStore | None = None,
        budget: Budget | None = None,
        kill_switch: KillSwitch | None = None,
        stop_token: StopToken | None = None,
        settings: Settings | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.observer = observer
        self.approval_gate = approval_gate or CliApprovalGate()
        self.settings = settings or Settings.load()
        self.run_store = run_store or RunStore()
        self.budget = budget or Budget(settings=self.settings)
        self.stop_token = stop_token or StopToken()
        self.kill_switch = kill_switch or KillSwitch(token=self.stop_token)
        self.event_bus = event_bus

    async def run(
        self,
        goal: str,
        autonomy_mode: str = "step",
        run_id: str | None = None,
    ) -> RunResult:
        rid = run_id or f"run-{int(datetime.now(UTC).timestamp())}"
        run_dir = self.run_store.create_run(
            run_id=rid,
            goal=goal,
            mode=autonomy_mode,
            settings_snapshot=self.settings.model_dump(),
        )
        state = TaskState(
            run_id=rid,
            goal=goal,
            autonomy_mode=autonomy_mode,
            status="RUNNING",
            current_step=0,
        )

        logger.info("agent_runner.run_started", run_id=rid, goal=goal, mode=autonomy_mode)

        if self.event_bus:
            await self.event_bus.publish(
                Event(
                    run_id=rid,
                    step_index=0,
                    type="run_started",
                    payload={"goal": goal, "mode": autonomy_mode},
                )
            )

        last_result: ActionResult | None = None
        run_dir = self.run_store.get_run_dir(rid)

        # Start KillSwitch
        with self.kill_switch:
            while True:
                # 1. Check StopToken
                if self.stop_token.is_set():
                    state.status = "STOPPED_BY_USER"
                    logger.warning("agent_runner.stopped", reason=self.stop_token.reason())
                    break

                # 2. Check Budget
                b_status = self.budget.check(state)
                if not b_status.ok:
                    state.status = "FAILED_BUDGET"
                    logger.warning("agent_runner.budget_exceeded", reason=b_status.reason)
                    break

                # 3. Observe Desktop
                obs = await asyncio.to_thread(
                    self.observer.observe,
                    last_result=last_result,
                    step_index=state.current_step,
                    run_id=rid,
                )

                # 4. Propose Next Action
                try:
                    plan_resp = await self.planner.propose(state=state, obs=obs)
                except Exception as e:
                    state.status = "FAILED_PROVIDER"
                    logger.error("agent_runner.planner_failed", error=str(e))
                    break

                # Clear feedback queue now that planner has processed it
                state.feedback_queue.clear()

                # 5. Check for terminal actions
                action = plan_resp.action
                if isinstance(action, DoneAction):
                    state.status = "COMPLETED"
                    logger.info("agent_runner.goal_completed", summary=action.summary)
                    self._record_step(
                        state=state,
                        obs=obs,
                        plan_resp=plan_resp,
                        result=ActionResult(
                            action_type="done",
                            success=True,
                            started_at=datetime.now(UTC),
                            duration_ms=0,
                            data={"summary": action.summary},
                        ),
                        approved=True,
                    )
                    break

                if isinstance(action, FailAction):
                    state.status = "ABORTED_BY_AGENT"
                    logger.warning("agent_runner.goal_aborted", reason=action.reason)
                    self._record_step(
                        state=state,
                        obs=obs,
                        plan_resp=plan_resp,
                        result=ActionResult(
                            action_type="fail",
                            success=False,
                            started_at=datetime.now(UTC),
                            duration_ms=0,
                            data={"reason": action.reason},
                        ),
                        approved=True,
                    )
                    break

                # 6. Safety & Approval Gate (In Phase 3, step mode asks for all actions)
                approved = await self.approval_gate.arequest(action)
                if not approved:
                    logger.info("agent_runner.action_denied", action_type=action.type)
                    state.feedback_queue.append(f"Action '{action.type}' was denied by human user.")
                    last_result = ActionResult(
                        action_type=action.type,
                        success=False,
                        started_at=datetime.now(UTC),
                        duration_ms=0,
                        data={"denied": True},
                    )
                    self._record_step(
                        state=state,
                        obs=obs,
                        plan_resp=plan_resp,
                        result=last_result,
                        approved=False,
                    )
                    state.current_step += 1
                    continue

                # 7. Execute Action
                mapper = CoordinateMapper(screen=obs.screen, image=obs.image)
                ctx = ExecutionContext(
                    run_id=rid,
                    stop=self.stop_token,
                    mapper=mapper,
                    settings=self.settings,
                    workdir=run_dir,
                )

                result = await self.executor.execute(
                    action=action,
                    ctx=ctx,
                    step_index=state.current_step,
                )
                last_result = result

                # 8. Record Step
                self._record_step(
                    state=state,
                    obs=obs,
                    plan_resp=plan_resp,
                    result=result,
                    approved=True,
                )
                state.current_step += 1

                # Write updated state to disk
                self.run_store.write_state(rid, state)

        # Generate summary.md
        summary_text = self._generate_summary(state)
        summary_path = run_dir / "summary.md"
        summary_path.write_text(summary_text, encoding="utf-8")

        if self.event_bus:
            await self.event_bus.publish(
                Event(
                    run_id=rid,
                    type="run_finished",
                    payload={"status": state.status, "steps": state.current_step},
                )
            )

        return RunResult(
            run_id=rid,
            status=state.status,
            steps_count=state.current_step,
            goal=goal,
            summary=summary_text,
        )

    def _record_step(
        self,
        state: TaskState,
        obs: Observation,
        plan_resp: Any,
        result: ActionResult,
        approved: bool,
    ) -> None:
        """Create StepRecord and append to state and persistence."""
        step_rec = StepRecord(
            step_index=state.current_step,
            observation_ref=obs.image.path_model or "",
            planner_response=plan_resp,
            verdict=Verdict(
                decision="allow" if approved else "needs_confirmation",
                tier="CONFIRM" if not approved else "SAFE",
                category="manual_approval",
                human_summary=f"Action {plan_resp.action.type}",
            ),
            approval=ApprovalDecision(
                decision="approved" if approved else "denied",
            ),
            result=result,
        )
        state.steps.append(step_rec)
        self.run_store.append_event(
            state.run_id,
            Event(
                run_id=state.run_id,
                step_index=state.current_step,
                type="step_completed",
                payload=step_rec.model_dump(),
            ),
        )

    def _generate_summary(self, state: TaskState) -> str:
        """Generate markdown summary of run."""
        lines = [
            f"# Run Summary: {state.run_id}",
            f"- **Goal**: {state.goal}",
            f"- **Final Status**: {state.status}",
            f"- **Steps Executed**: {state.current_step}",
            f"- **Timestamp**: {datetime.now(UTC).isoformat()}",
            "",
            "## Steps History",
        ]
        for step in state.steps:
            res_str = "SUCCESS" if step.result.success else "FAILURE"
            act_type = step.planner_response.action.type
            lines.append(f"- **Step {step.step_index}**: `{act_type}` -> {res_str}")
        return "\n".join(lines)
