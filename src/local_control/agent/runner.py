import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
    ErrorInfo,
    Observation,
    RunPermissions,
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
from local_control.safety.validator import SafetyValidator

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
        validator: SafetyValidator | None = None,
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
        self.validator = validator or SafetyValidator(settings=self.settings)
        self.run_store = run_store or RunStore()
        self.budget = budget or Budget(settings=self.settings)
        self.stop_token = stop_token or StopToken()
        self.kill_switch = kill_switch or KillSwitch(token=self.stop_token)
        self.event_bus = event_bus

    def _write_audit(
        self,
        run_dir: Path,
        event_type: str,
        payload: dict[str, Any],
        step_index: int = 0,
    ) -> None:
        """Synchronously write an append-only audit record to run_dir and global audit log."""
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "step_index": step_index,
            "payload": payload,
        }
        line = json.dumps(record) + "\n"

        # 1. Per-run audit log
        audit_file = run_dir / "audit.jsonl"
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(line)

        # 2. Global audit log in %LOCALAPPDATA%/local-control/audit/global.jsonl
        try:
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            if local_appdata:
                global_audit_dir = Path(local_appdata) / "local-control" / "audit"
                global_audit_dir.mkdir(parents=True, exist_ok=True)
                global_audit_file = global_audit_dir / "global.jsonl"
                with global_audit_file.open("a", encoding="utf-8") as gf:
                    gf.write(line)
        except Exception as err:
            logger.warning("agent_runner.global_audit_write_failed", error=str(err))

    async def run(
        self,
        goal: str,
        autonomy_mode: str = "assisted",
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
        permissions = RunPermissions()
        self.validator.reset_run()

        logger.info("agent_runner.run_started", run_id=rid, goal=goal, mode=autonomy_mode)
        self._write_audit(
            run_dir,
            "run_started",
            {"run_id": rid, "goal": goal, "mode": autonomy_mode},
            step_index=0,
        )

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
        step_failures: dict[int, int] = {}

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

                # Update state plan if returned
                if plan_resp.plan:
                    state.plan = plan_resp.plan

                # Clear feedback queue now that planner has processed it
                state.feedback_queue.clear()

                # Check for replan trigger: assessment failure twice on the same step
                active_step_idx = state.plan.current_index if state.plan else 0
                if plan_resp.assessment.previous_action_outcome == "failure":
                    step_failures[active_step_idx] = step_failures.get(active_step_idx, 0) + 1
                    logger.warning(
                        "agent_runner.step_failure_recorded",
                        step_index=active_step_idx,
                        failures=step_failures[active_step_idx],
                        evidence=plan_resp.assessment.evidence,
                    )
                    if step_failures[active_step_idx] >= 2:
                        logger.info(
                            "agent_runner.replan_triggered",
                            step_index=active_step_idx,
                            failures=step_failures[active_step_idx],
                        )
                        replan_reason = (
                            f"Step {active_step_idx} failed {step_failures[active_step_idx]} times consecutively: "
                            f"{plan_resp.assessment.evidence}"
                        )
                        try:
                            plan_resp = await self.planner.propose(
                                state=state,
                                obs=obs,
                                replan_reason=replan_reason,
                            )
                            if plan_resp.plan:
                                state.plan = plan_resp.plan
                            step_failures[state.plan.current_index if state.plan else 0] = 0
                        except Exception as e:
                            state.status = "FAILED_PROVIDER"
                            logger.error("agent_runner.replan_failed", error=str(e))
                            break
                elif plan_resp.assessment.previous_action_outcome == "success":
                    step_failures[active_step_idx] = 0

                # Log active plan step
                if (
                    state.plan
                    and state.plan.steps
                    and 0 <= state.plan.current_index < len(state.plan.steps)
                ):
                    ps = state.plan.steps[state.plan.current_index]
                    logger.info(
                        "agent_runner.active_plan_step",
                        revision=state.plan.revision,
                        step_index=ps.index,
                        description=ps.description,
                        status=ps.status,
                    )

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

                # 6. SafetyValidator Gate
                verdict = self.validator.validate(
                    action=action,
                    obs=obs,
                    permissions=permissions,
                    mode=autonomy_mode,
                    confidence=plan_resp.confidence,
                )
                self._write_audit(
                    run_dir,
                    "verdict",
                    verdict.model_dump(),
                    step_index=state.current_step,
                )

                if verdict.decision == "blocked":
                    reasons_str = "; ".join(verdict.reasons)
                    logger.warning(
                        "agent_runner.action_blocked",
                        action_type=action.type,
                        category=verdict.category,
                        reasons=verdict.reasons,
                    )
                    self._write_audit(
                        run_dir,
                        "blocked_attempt",
                        {"action": action.model_dump(), "verdict": verdict.model_dump()},
                        step_index=state.current_step,
                    )
                    state.feedback_queue.append(
                        f"Action '{action.type}' was BLOCKED by safety policy ({verdict.category}): {reasons_str}."
                    )
                    blocked_result = ActionResult(
                        action_type=action.type,
                        success=False,
                        started_at=datetime.now(UTC),
                        duration_ms=0,
                        error=ErrorInfo(
                            code=verdict.category,
                            message=f"Action blocked: {reasons_str}",
                        ),
                    )
                    last_result = blocked_result
                    self._record_step(
                        state=state,
                        obs=obs,
                        plan_resp=plan_resp,
                        result=blocked_result,
                        approved=False,
                        verdict=verdict,
                        approval=ApprovalDecision(
                            decision="denied", note="blocked_by_safety_policy"
                        ),
                    )
                    state.current_step += 1
                    continue

                if verdict.decision == "needs_confirmation":
                    approval_dec = await self.approval_gate.arequest(
                        action=action,
                        verdict=verdict,
                        screenshot_path=obs.image.path_model,
                    )
                    self._write_audit(
                        run_dir,
                        "approval",
                        approval_dec.model_dump(),
                        step_index=state.current_step,
                    )
                    if approval_dec.decision in ("approved", "approved_for_run"):
                        if approval_dec.decision == "approved_for_run":
                            permissions.granted_categories.add(verdict.category)
                            logger.info(
                                "agent_runner.category_granted_for_run",
                                category=verdict.category,
                            )
                    else:
                        if approval_dec.note == "Stopped by user":
                            self.stop_token.set("Stopped by user during approval prompt")
                        logger.info("agent_runner.action_denied", action_type=action.type)
                        state.feedback_queue.append(
                            f"Action '{action.type}' was denied by human user."
                        )
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
                            verdict=verdict,
                            approval=approval_dec,
                        )
                        state.current_step += 1
                        continue
                else:
                    # Allow (SAFE in assisted/trusted, or granted CONFIRM in trusted)
                    approval_dec = ApprovalDecision(decision="approved", note="auto_allow")

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

                if verdict.tier == "CONFIRM":
                    self._write_audit(
                        run_dir,
                        "executed_confirm",
                        {"action": action.model_dump(), "result": result.model_dump()},
                        step_index=state.current_step,
                    )

                # 8. Record Step
                self._record_step(
                    state=state,
                    obs=obs,
                    plan_resp=plan_resp,
                    result=result,
                    approved=True,
                    verdict=verdict,
                    approval=approval_dec,
                )
                state.current_step += 1

                # Write updated state to disk
                self.run_store.write_state(rid, state)

        # Generate summary.md
        summary_text = self._generate_summary(state)
        summary_path = run_dir / "summary.md"
        summary_path.write_text(summary_text, encoding="utf-8")

        self._write_audit(
            run_dir,
            "run_finished",
            {"status": state.status, "steps": state.current_step},
            step_index=state.current_step,
        )

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
        verdict: Verdict | None = None,
        approval: ApprovalDecision | None = None,
    ) -> None:
        """Create StepRecord and append to state and persistence."""
        v = verdict or Verdict(
            decision="allow" if approved else "needs_confirmation",
            tier="SAFE" if approved else "CONFIRM",
            category="manual_approval",
            human_summary=f"Action {plan_resp.action.type}",
        )
        app = approval or ApprovalDecision(
            decision="approved" if approved else "denied",
        )
        step_rec = StepRecord(
            step_index=state.current_step,
            observation_ref=obs.image.path_model or "",
            planner_response=plan_resp,
            verdict=v,
            approval=app,
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
        ]
        if state.plan:
            lines.append("")
            lines.append("## Execution Plan")
            lines.append(f"- **Revision**: {state.plan.revision}")
            lines.append(f"- **Active Step Index**: {state.plan.current_index}")
            for ps in state.plan.steps:
                lines.append(f"- Step {ps.index} [{ps.status}]: {ps.description}")

        lines.extend(
            [
                "",
                "## Steps History",
            ]
        )
        for step in state.steps:
            res_str = "SUCCESS" if step.result.success else "FAILURE"
            act_type = step.planner_response.action.type
            lines.append(f"- **Step {step.step_index}**: `{act_type}` -> {res_str}")
        return "\n".join(lines)
