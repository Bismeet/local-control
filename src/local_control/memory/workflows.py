"""Workflow recording, parameterization, and replaying for local-control."""

import json
from typing import TYPE_CHECKING, Any, Literal

import structlog

from local_control.agent.planner import Planner

if TYPE_CHECKING:
    from local_control.agent.runner import RunResult
from local_control.config.settings import Settings
from local_control.core.actions import Action, ActionAdapter, DoneAction
from local_control.core.events import EventBus
from local_control.core.run_store import RunStore
from local_control.core.types import (
    Plan,
    PlanStep,
    StepRecord,
    TaskState,
)
from local_control.execution.executor import Executor
from local_control.execution.tools.filesystem_tool import FilesystemTool
from local_control.execution.tools.input_tool import InputTool
from local_control.execution.tools.observation_tool import ObservationTool
from local_control.execution.tools.terminal_tool import TerminalTool
from local_control.execution.tools.wait_tool import WaitTool
from local_control.execution.tools.window_tool import WindowTool
from local_control.memory.models import Workflow
from local_control.memory.sanitizer import Sanitizer
from local_control.memory.store import MemoryStore
from local_control.models.provider import ModelRequest, ModelResponse, Usage
from local_control.observation.observer import Observer
from local_control.safety.approval import ApprovalGate, CliApprovalGate
from local_control.safety.kill_switch import StopToken
from local_control.safety.validator import SafetyValidator

logger = structlog.get_logger(__name__)


class WorkflowRecorder:
    """Extracts, sanitizes, and records reusable workflow templates from completed runs."""

    def __init__(self, sanitizer: Sanitizer | None = None) -> None:
        self.sanitizer = sanitizer or Sanitizer()

    def record_from_run(
        self,
        name: str,
        goal: str,
        steps: list[StepRecord],
        description: str = "",
        store: MemoryStore | None = None,
    ) -> Workflow:
        """Create a sanitized workflow template from run steps."""
        params: dict[str, str] = {}

        # 1. Parameterize goal
        goal_cleaned = self.sanitizer.sanitize_secrets(goal)
        goal_template = self.sanitizer.parameterize_paths(goal_cleaned, params)

        # 2. Extract and sanitize substantive actions that succeeded
        action_dicts: list[dict[str, Any]] = []
        for step in steps:
            if step.result and not step.result.success:
                continue
            act = step.planner_response.action
            act_dict = act.model_dump(mode="json")
            sanitized_act = self.sanitizer.sanitize_action(act_dict, params)
            action_dicts.append(sanitized_act)

        steps_json = json.dumps(action_dicts, indent=2)
        params_json = json.dumps(params, indent=2)

        if store:
            store.save_workflow(
                name=name,
                goal_template=goal_template,
                steps_json=steps_json,
                params_json=params_json,
                description=description,
            )
            wf = store.get_workflow(name)
            if wf:
                return wf

        import datetime

        now = datetime.datetime.now(datetime.UTC).isoformat()
        return Workflow(
            name=name,
            description=description,
            goal_template=goal_template,
            steps_json=steps_json,
            params_json=params_json,
            created_at=now,
            updated_at=now,
        )

    def record_from_task_state(
        self,
        name: str,
        task_state: TaskState,
        description: str = "",
        store: MemoryStore | None = None,
    ) -> Workflow:
        """Record workflow from an existing TaskState."""
        return self.record_from_run(
            name=name,
            goal=task_state.goal,
            steps=task_state.steps,
            description=description,
            store=store,
        )


class WorkflowReplayProvider:
    """ModelProvider adapter that serves pre-recorded workflow actions into the agent runner."""

    name: str = "workflow_replay"
    model: str = "replay_template"
    supports_vision: bool = True
    supports_json_schema: bool = True

    def __init__(self, actions: list[Action], plan: Plan) -> None:
        self.actions = list(actions)
        self.plan = plan
        self.step_pointer = 0

    async def complete(self, req: ModelRequest) -> ModelResponse:
        """Return next recorded action wrapped in PlannerResponse structure."""
        if self.step_pointer < len(self.actions):
            act = self.actions[self.step_pointer]
            updated_steps = []
            for s in self.plan.steps:
                st: Literal["pending", "active", "done", "failed", "skipped"] = (
                    "done"
                    if s.index < self.step_pointer
                    else ("active" if s.index == self.step_pointer else "pending")
                )
                updated_steps.append(PlanStep(index=s.index, description=s.description, status=st))
            cur_plan = Plan(
                steps=updated_steps,
                current_index=self.step_pointer,
                revision=self.plan.revision,
            )
            resp_dict = {
                "assessment": {
                    "screen_summary": f"Executing workflow step {self.step_pointer + 1}/{len(self.actions)}: {act.type}",
                    "previous_action_outcome": "success"
                    if self.step_pointer > 0
                    else "not_applicable",
                    "evidence": "Replaying recorded workflow sequence",
                },
                "plan": cur_plan.model_dump(mode="json"),
                "action": act.model_dump(mode="json"),
                "confidence": 0.99,
                "rationale": f"Replay step {self.step_pointer + 1}: {act.target_description or act.type}",
            }
            self.step_pointer += 1
            return ModelResponse(
                text=json.dumps(resp_dict),
                parsed=resp_dict,
                usage=Usage(input_tokens=10, output_tokens=10, cost_usd=0.0),
                latency_ms=1,
                provider="workflow_replay",
                model="replay_template",
            )
        else:
            done_act = DoneAction(
                summary="Workflow replay execution completed successfully.",
                verification_notes="All recorded workflow actions replayed.",
                target_description="Workflow completion",
                expected_outcome="Workflow finished",
            )
            completed_steps = [
                PlanStep(index=s.index, description=s.description, status="done")
                for s in self.plan.steps
            ]
            cur_plan = Plan(
                steps=completed_steps,
                current_index=len(completed_steps) - 1 if completed_steps else 0,
                revision=self.plan.revision,
            )
            resp_dict = {
                "assessment": {
                    "screen_summary": "All workflow actions completed.",
                    "previous_action_outcome": "success",
                    "evidence": "Workflow sequence complete",
                },
                "plan": cur_plan.model_dump(mode="json"),
                "action": done_act.model_dump(mode="json"),
                "confidence": 1.0,
                "rationale": "Workflow replay completed.",
            }
            return ModelResponse(
                text=json.dumps(resp_dict),
                parsed=resp_dict,
                usage=Usage(input_tokens=5, output_tokens=5, cost_usd=0.0),
                latency_ms=1,
                provider="workflow_replay",
                model="replay_template",
            )


class WorkflowReplayer:
    """Prepares and executes a parameterized workflow through the safety and approval pipeline."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def prepare_replay(
        self,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[Workflow, str, list[Action], Plan]:
        """Load workflow, substitute parameters, validate typed actions, and build initial Plan."""
        wf = self.store.get_workflow(name)
        if not wf:
            raise ValueError(f"Workflow '{name}' not found in memory store.")

        rendered_goal, rendered_step_dicts = wf.render(params)

        validated_actions: list[Action] = []
        plan_steps: list[PlanStep] = []

        for act_dict in rendered_step_dicts:
            action_obj = ActionAdapter.validate_python(act_dict)
            if isinstance(action_obj, DoneAction):
                continue
            idx = len(plan_steps)
            validated_actions.append(action_obj)
            desc = action_obj.target_description or f"Step {idx + 1}: {action_obj.type}"
            plan_steps.append(
                PlanStep(
                    index=idx,
                    description=desc,
                    status="active" if idx == 0 else "pending",
                )
            )

        initial_plan = Plan(
            steps=plan_steps,
            current_index=0,
            revision=0,
        )

        return wf, rendered_goal, validated_actions, initial_plan

    async def run(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        autonomy_mode: str = "assisted",
        executor: Executor | None = None,
        observer: Observer | None = None,
        approval_gate: ApprovalGate | None = None,
        validator: SafetyValidator | None = None,
        settings: Settings | None = None,
        run_store: RunStore | None = None,
        event_bus: EventBus | None = None,
        stop_token: StopToken | None = None,
    ) -> "RunResult":
        """Run workflow replay end-to-end through the standard AgentRunner and safety pipeline."""
        from local_control.agent.runner import AgentRunner

        wf, rendered_goal, actions, initial_plan = self.prepare_replay(name, params)

        cfg = settings or Settings.load()
        replay_provider = WorkflowReplayProvider(actions=actions, plan=initial_plan)
        planner = Planner(provider=replay_provider)

        exec_instance = executor or Executor(
            tools=[
                InputTool(),
                WindowTool(),
                WaitTool(),
                ObservationTool(),
                FilesystemTool(),
                TerminalTool(),
            ]
        )
        obs_instance = observer or Observer(settings=cfg)
        gate = approval_gate or CliApprovalGate()
        val = validator or SafetyValidator(settings=cfg)
        rs = run_store or RunStore()
        st = stop_token or StopToken()

        runner = AgentRunner(
            planner=planner,
            executor=exec_instance,
            observer=obs_instance,
            approval_gate=gate,
            validator=val,
            settings=cfg,
            run_store=rs,
            event_bus=event_bus,
            stop_token=st,
            memory_store=self.store,
        )

        result = await runner.run(
            goal=rendered_goal,
            autonomy_mode=autonomy_mode,
        )

        if result.status == "COMPLETED":
            self.store.increment_workflow_success(wf.name)
            if wf.id is not None:
                self.store.record_workflow_run(
                    workflow_id=wf.id,
                    run_id=result.run_id,
                    status=result.status,
                )

        return result
