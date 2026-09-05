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
from local_control.agent.recovery import RecoveryPolicy
from local_control.agent.stuck_detector import StuckDetector
from local_control.agent.verifier import Verifier, verify_done_proposal
from local_control.config.settings import Settings
from local_control.core.actions import Action, AskUserAction, DoneAction, FailAction, Rect
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
from local_control.memory.store import MemoryStore
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
        verifier: Verifier | None = None,
        recovery_policy: RecoveryPolicy | None = None,
        stuck_detector: StuckDetector | None = None,
        memory_store: MemoryStore | None = None,
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
        self.verifier = verifier or Verifier(phash_threshold=self.settings.verify.phash_threshold)
        self.stuck_detector = stuck_detector or StuckDetector(
            repetition_threshold=self.settings.verify.stuck_threshold,
            phash_threshold=self.settings.verify.phash_threshold,
        )
        self.recovery_policy = recovery_policy or RecoveryPolicy(
            max_retries_per_step=self.settings.verify.max_retries_per_step
        )
        self.memory_store = memory_store

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
        line = json.dumps(record, default=str) + "\n"

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
            settings_snapshot=self.settings.model_dump(mode="json"),
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
        self.budget.reset()
        self.recovery_policy.reset()
        self.stuck_detector.reset()

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
        last_action: Action | None = None
        last_obs: Observation | None = None
        zoom_rect: Rect | None = None
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

                if self.event_bus:
                    await self.event_bus.publish(
                        Event(
                            run_id=rid,
                            step_index=state.current_step,
                            type="step_started",
                            payload={"step_index": state.current_step},
                        )
                    )

                # 3. Observe Desktop
                obs = await asyncio.to_thread(
                    self.observer.observe,
                    last_result=last_result,
                    step_index=state.current_step,
                    run_id=rid,
                    zoom_rect=zoom_rect,
                )
                zoom_rect = None

                # Enrich with browser observation if BrowserTool is active
                b_tool = self.executor.get_tool("browser_navigate")
                if b_tool is not None and hasattr(b_tool, "get_observation"):
                    b_obs = await b_tool.get_observation()
                    if b_obs is not None:
                        obs = obs.model_copy(update={"browser": b_obs})

                # 4. Propose Next Action
                hints: list[Any] = []
                if self.memory_store is not None:
                    try:
                        active_app = (
                            obs.foreground.process_name
                            if obs.foreground and obs.foreground.process_name
                            else None
                        )
                        hints = self.memory_store.search_hints(
                            query=state.goal, app=active_app, limit=5
                        )
                    except Exception as e:
                        logger.warning("agent_runner.memory_hint_lookup_failed", error=str(e))

                try:
                    plan_resp = await self.planner.propose(state=state, obs=obs, hints=hints)
                except Exception as e:
                    state.status = "FAILED_PROVIDER"
                    logger.error("agent_runner.planner_failed", error=str(e))
                    break

                # 5. Verify Previous Action & Run Recovery Ladder
                active_step_idx = state.plan.current_index if state.plan else 0
                if last_action is not None and last_result is not None and last_obs is not None:
                    verif_result = self.verifier.verify(
                        action=last_action,
                        result=last_result,
                        obs_before=last_obs,
                        obs_after=obs,
                        assessment=plan_resp.assessment,
                    )
                    if state.steps:
                        state.steps[-1].verification = verif_result

                    # Stuck detection: record screen and check progress
                    expects_chg = bool(getattr(last_action, "expected_outcome", ""))
                    if last_action.type in ("click", "drag", "type_text", "press_keys"):
                        expects_chg = True
                    self.stuck_detector.record_screen(obs.image.phash, expects_change=expects_chg)
                    is_stuck, stuck_reason = self.stuck_detector.check_stuck()

                    if verif_result.outcome == "success":
                        self.stuck_detector.record_progress()

                    # Track planner proposal confidence
                    self.recovery_policy.record_proposal_confidence(plan_resp.confidence)

                    # Decide recovery action
                    recovery_dec = self.recovery_policy.decide(
                        verification=verif_result,
                        step_index=active_step_idx,
                        is_stuck=is_stuck,
                        stuck_reason=stuck_reason,
                        user_stopped=self.stop_token.is_set(),
                    )

                    # Emit verification and recovery events
                    if self.event_bus:
                        await self.event_bus.publish(
                            Event(
                                run_id=rid,
                                step_index=state.current_step,
                                type="verification_result",
                                payload=verif_result.model_dump(),
                            )
                        )
                        await self.event_bus.publish(
                            Event(
                                run_id=rid,
                                step_index=state.current_step,
                                type="recovery_decision",
                                payload=recovery_dec.model_dump(),
                            )
                        )

                    self._write_audit(
                        run_dir,
                        "verification",
                        verif_result.model_dump(),
                        step_index=state.current_step,
                    )
                    self._write_audit(
                        run_dir,
                        "recovery",
                        recovery_dec.model_dump(),
                        step_index=state.current_step,
                    )

                    # Act on recovery decision:
                    if recovery_dec.kind == "abort":
                        state.status = "ABORTED_BY_AGENT"
                        logger.warning("agent_runner.aborted_by_recovery", hint=recovery_dec.hint)
                        break

                    if recovery_dec.kind == "ask_user":
                        state.status = "WAITING_USER"
                        logger.info("agent_runner.waiting_user", reason="recovery_escalation")
                        if self.event_bus:
                            await self.event_bus.publish(
                                Event(
                                    run_id=rid,
                                    step_index=state.current_step,
                                    type="waiting_user",
                                    payload={"reason": recovery_dec.hint},
                                )
                            )
                        prompt_msg = (
                            recovery_dec.hint or "Agent requested human assistance to continue."
                        )
                        user_ans = await self.approval_gate.aask_user(prompt_msg)
                        state.feedback_queue.append(f"User assistance provided: {user_ans}")
                        state.status = "RUNNING"

                    elif recovery_dec.kind == "replan":
                        logger.info("agent_runner.replan_triggered", hint=recovery_dec.hint)
                        try:
                            plan_resp = await self.planner.propose(
                                state=state,
                                obs=obs,
                                replan_reason=recovery_dec.hint,
                            )
                            if plan_resp.plan:
                                state.plan = plan_resp.plan
                        except Exception as e:
                            state.status = "FAILED_PROVIDER"
                            logger.error("agent_runner.replan_failed", error=str(e))
                            break

                    elif recovery_dec.kind == "retry_hint":
                        if recovery_dec.hint:
                            state.feedback_queue.append(recovery_dec.hint)

                # Update state plan if returned
                if plan_resp.plan:
                    state.plan = plan_resp.plan

                # Clear feedback queue now that planner has processed it
                state.feedback_queue.clear()

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

                # 6. Check for terminal / special actions
                action = plan_resp.action
                if isinstance(action, DoneAction):
                    is_valid, reject_reason = verify_done_proposal(plan_resp, state.plan)
                    if not is_valid:
                        logger.warning("agent_runner.done_rejected", reason=reject_reason)
                        state.feedback_queue.append(reject_reason)
                        rejected_result = ActionResult(
                            action_type="done",
                            success=False,
                            started_at=datetime.now(UTC),
                            duration_ms=0,
                            error=ErrorInfo(code="DONE_REJECTED", message=reject_reason),
                        )
                        self._record_step(
                            state=state,
                            obs=obs,
                            plan_resp=plan_resp,
                            result=rejected_result,
                            approved=False,
                        )
                        last_obs = obs
                        last_action = action
                        last_result = rejected_result
                        state.current_step += 1
                        continue

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

                if isinstance(action, AskUserAction):
                    state.status = "WAITING_USER"
                    logger.info("agent_runner.ask_user", question=action.question)
                    if self.event_bus:
                        await self.event_bus.publish(
                            Event(
                                run_id=rid,
                                step_index=state.current_step,
                                type="waiting_user",
                                payload={"question": action.question},
                            )
                        )
                    answer = await self.approval_gate.aask_user(action.question)
                    state.feedback_queue.append(f"User response to '{action.question}': {answer}")
                    state.status = "RUNNING"
                    ask_result = ActionResult(
                        action_type="ask_user",
                        success=True,
                        started_at=datetime.now(UTC),
                        duration_ms=0,
                        data={"question": action.question, "answer": answer},
                    )
                    self._record_step(
                        state=state,
                        obs=obs,
                        plan_resp=plan_resp,
                        result=ask_result,
                        approved=True,
                    )
                    last_obs = obs
                    last_action = action
                    last_result = ask_result
                    state.current_step += 1
                    continue

                if action.type == "zoom_region":
                    zoom_rect = getattr(action, "rect", None)

                # Record action to stuck detector
                self.stuck_detector.record_action(action)

                # 7. SafetyValidator Gate
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
                    last_obs = obs
                    last_action = action
                    last_result = blocked_result
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
                        denied_result = ActionResult(
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
                            result=denied_result,
                            approved=False,
                            verdict=verdict,
                            approval=approval_dec,
                        )
                        last_obs = obs
                        last_action = action
                        last_result = denied_result
                        state.current_step += 1
                        continue
                else:
                    # Allow (SAFE in assisted/trusted, or granted CONFIRM in trusted)
                    approval_dec = ApprovalDecision(decision="approved", note="auto_allow")

                # 8. Execute Action
                mapper = CoordinateMapper(screen=obs.screen, image=obs.image)
                ctx = ExecutionContext(
                    run_id=rid,
                    stop=self.stop_token,
                    mapper=mapper,
                    settings=self.settings,
                    workdir=run_dir,
                    ui_elements=obs.ui_elements,
                )

                if self.event_bus:
                    await self.event_bus.publish(
                        Event(
                            run_id=rid,
                            step_index=state.current_step,
                            type="action_started",
                            payload={"action": action.model_dump(mode="json")},
                        )
                    )

                result = await self.executor.execute(
                    action=action,
                    ctx=ctx,
                    step_index=state.current_step,
                )
                last_obs = obs
                last_action = action
                last_result = result

                if self.event_bus:
                    await self.event_bus.publish(
                        Event(
                            run_id=rid,
                            step_index=state.current_step,
                            type="action_finished",
                            payload={
                                "action": action.model_dump(mode="json"),
                                "result": result.model_dump(mode="json"),
                            },
                        )
                    )

                if verdict.tier == "CONFIRM":
                    self._write_audit(
                        run_dir,
                        "executed_confirm",
                        {
                            "action": action.model_dump(mode="json"),
                            "result": result.model_dump(mode="json"),
                        },
                        step_index=state.current_step,
                    )

                # 9. Record Step
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

        if self.memory_store is not None:
            try:
                self.memory_store.index_run(
                    run_id=rid,
                    goal=state.goal,
                    status=state.status,
                    step_count=state.current_step,
                )
            except Exception as e:
                logger.warning("agent_runner.memory_index_failed", error=str(e))

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
        ev = Event(
            run_id=state.run_id,
            step_index=state.current_step,
            type="step_completed",
            payload=step_rec.model_dump(mode="json"),
        )
        self.run_store.append_event(state.run_id, ev)
        if self.event_bus:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.event_bus.publish(ev))
            except RuntimeError:
                pass

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

        if state.steps:
            last_action = state.steps[-1].planner_response.action
            if isinstance(last_action, DoneAction):
                lines.extend(
                    [
                        "",
                        "## Completion Summary",
                        last_action.summary,
                        "",
                        "### Verification Notes",
                        last_action.verification_notes,
                    ]
                )
            elif isinstance(last_action, FailAction):
                lines.extend(["", "## Failure Reason", last_action.reason])

        return "\n".join(lines)
