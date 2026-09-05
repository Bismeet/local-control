# local-control: Personal Computer Agent - Master Plan

> **Status:** Authoritative planning document. Single source of truth. All other documents in `/docs` derive from this one; if they disagree, this document wins and the other must be corrected.
>
> **Repository state at time of writing:** the repository contains a single commit with only the GitLab template `README.md`. There is no source code, no language, no build tooling, no CI configuration and no dependencies. Every technology choice below is therefore a fresh decision, justified explicitly in the *Critical Decisions* section.

Related documents:

- [ARCHITECTURE.md](./ARCHITECTURE.md) - technical architecture, contracts, diagrams
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) - phased, executable roadmap
- [SECURITY_MODEL.md](./SECURITY_MODEL.md) - permission tiers, threat model, controls
- [TEST_PLAN.md](./TEST_PLAN.md) - test strategy and end-to-end scenarios

---

## 1. Project Vision

`local-control` is a **local-first Personal Computer Agent for Windows**. A user states a goal in natural language ("organize my Downloads folder by file type", "clone this repo and tell me why the tests fail"). The agent observes the screen and system state, reasons with a vision-capable LLM, proposes one structured action at a time, has each action validated by a deterministic safety layer, executes it, observes the result, verifies progress and iterates until the goal is verifiably complete or it must stop and ask the human.

The agent is a **careful operator**, not an unrestricted automation script: the LLM never controls the machine directly. It proposes typed actions; code decides whether and how they run.

## 2. Problem Statement

General-purpose desktop automation today is either:

1. **Scripted** (AutoHotkey, PowerShell, RPA) - brittle, no understanding of screen state, cannot adapt when the UI changes.
2. **LLM "code-gen and run"** - the model writes arbitrary code that is executed on the host. Powerful, but unsafe: no bounded action surface, no reliable audit, no way to gate destructive operations.
3. **Hosted computer-use demos** - impressive but not local, not Windows-first, not user-controllable, and usually without a serious permission model.

We need an agent that (a) understands what is on screen, (b) acts through a **small, typed, auditable action vocabulary**, (c) detects its own failures instead of blindly continuing, (d) always keeps the human in control of risky operations, and (e) is swappable across LLM providers.

## 3. Goals

1. Complete realistic multi-step desktop tasks on Windows 10/11 with GUI, filesystem, terminal and browser interaction.
2. Every OS-affecting operation flows through: **propose -> validate -> (approve) -> execute -> verify**.
3. Recognize action failure and recover (retry, replan, ask the user, or abort) instead of continuing blindly.
4. Provider-agnostic reasoning: replace the LLM by changing configuration, not code.
5. Full observability: every observation, proposal, decision, action and result is logged and replayable.
6. Human control at all times: kill switch, step mode, approval gates, hard-blocked action classes.
7. Simple, maintainable, single-process architecture that one developer (or one coding agent) can build incrementally.

## 4. Non-Goals

- Not a cloud service, not multi-tenant, no remote control of other machines.
- No arbitrary LLM-generated code execution on the host (ever). Shell commands exist as a **typed, gated tool**, not as a free code runner.
- No fully unattended operation of BLOCKED-tier actions (payments, credential entry, permanent deletion, etc.). See [SECURITY_MODEL.md](./SECURITY_MODEL.md).
- No macOS/Linux support in V1-V3 (abstractions leave the door open; nothing is built for them).
- No game automation, DRM-protected content, or anti-cheat environments.
- No training or fine-tuning of models.
- No microservices, message brokers, Kubernetes, external databases or cloud infrastructure.

## 5. Core User Experience

1. The user launches the agent (`local-control run "<goal>"` from a terminal in the MVP; the Control Center web UI after Phase 9).
2. The agent states its understanding of the goal and a rough plan.
3. It works step by step. For each step the user can see: current step, the screenshot the agent looked at, the action it proposes, its confidence and rationale.
4. SAFE actions run automatically. CONFIRM actions pause with a precise, human-readable description ("Move 14 files from `Downloads` to `Downloads\\PDFs`") and wait for **y/n**. BLOCKED actions are refused and the agent explains why and asks the user to do that part manually.
5. When the agent is stuck or unsure, it asks a specific question and waits.
6. The user can stop everything instantly with a global hotkey, by slamming the mouse into a screen corner, or with the Stop button in the Control Center.
7. At the end the agent reports what it did, what it verified, and what it could not do. The full run is stored for replay.

## 6. Functional Requirements

Tagged **[MVP]** (must have), **[SHOULD]** (should have, V2), **[FUTURE]** (V3+).

| ID | Requirement | Priority |
|----|-------------|----------|
| F1 | Accept a natural-language goal from CLI | MVP |
| F2 | Capture screenshots of the primary monitor with correct DPI handling | MVP |
| F3 | Enumerate top-level windows (title, process, rect, focus state) and focus a window by handle | MVP |
| F4 | Send mouse actions: move, click (left/right/middle, single/double), drag, scroll | MVP |
| F5 | Send keyboard actions: type Unicode text, press key combinations (hotkeys) | MVP |
| F6 | Build an Observation (screenshot + window info + last action result) and send it to a vision LLM | MVP |
| F7 | Receive a strictly typed ActionProposal from the model; reject malformed output and re-prompt | MVP |
| F8 | Deterministic SafetyValidator classifies each proposal into SAFE / CONFIRM / BLOCKED | MVP |
| F9 | Approval gate (CLI y/n) for CONFIRM; refusal for BLOCKED | MVP |
| F10 | Executor performs validated actions through tool adapters | MVP |
| F11 | Post-action verification: model assessment of expected vs. observed outcome + deterministic checks | MVP |
| F12 | Stuck/loop detection, step and time budgets, consecutive-failure limit | MVP |
| F13 | Recovery ladder: retry variant -> replan -> ask user -> abort | MVP |
| F14 | Explicit plan with steps and replanning when a step fails or the screen contradicts the plan | MVP |
| F15 | Filesystem tool: list, read, write, mkdir, move, copy, delete-to-recycle-bin, inside allowed roots | MVP |
| F16 | Terminal tool: run a non-interactive PowerShell command with timeout, cwd and output capture | MVP |
| F17 | Emergency stop: global hotkey, mouse-corner failsafe, stop file | MVP |
| F18 | Persist every run (events, screenshots, state) to a run directory; replay CLI | MVP |
| F19 | Model provider abstraction with at least an OpenAI-compatible provider and a scripted fake provider | MVP |
| F20 | OCR of screen regions as an optional observation enrichment | SHOULD |
| F21 | UI Automation element tree (UIA) as optional observation and targeting aid | SHOULD |
| F22 | Browser tool via Playwright: navigate, click, type, read text, snapshot, tabs | SHOULD |
| F23 | Control Center web UI: goal, plan, current step, reasoning status, actions, errors, live screen preview, approve/deny, stop | SHOULD |
| F24 | `ask_user` action for clarifications mid-task | MVP |
| F25 | Persistent memory: user preferences, learned app hints, successful workflow recordings | FUTURE |
| F26 | Reusable workflows: replay a recorded successful task with parameterization | FUTURE |
| F27 | Separate planner / executor / verifier model roles (different models per role) | FUTURE |
| F28 | Multi-monitor observation and action | SHOULD |

## 7. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| N1 | **Safety before capability.** No action executes without passing the validator. No configuration flag disables BLOCKED. |
| N2 | **Determinism where possible.** Validator, executor, coordinate mapping and policy rules are pure code with unit tests. Only the planner is probabilistic. |
| N3 | **Latency.** Observation build < 300 ms (excluding OCR). Full loop iteration dominated by model latency; target < 8 s per step with a typical hosted vision model. |
| N4 | **Cost control.** One model call per step by default. Screenshots downscaled to <= 1280 px width for the model. Token and cost counters per run. |
| N5 | **Observability.** Every event is a typed record in `events.jsonl`; every screenshot stored; replay reproduces the timeline offline. |
| N6 | **Replaceable model.** Provider chosen by config. No provider-specific types leak past `models/`. |
| N7 | **Single process, single machine.** asyncio event loop + worker threads. No network services except the local Control Center bound to `127.0.0.1`. |
| N8 | **Testable without a desktop.** Core loop, planner parsing, validator and policy run on Linux CI with fake providers and a fake computer. Desktop-dependent tests are marked and run on a real Windows session. |
| N9 | **Privacy.** Screenshots and logs stay local. Only the current observation is sent to the configured model provider. Clipboard is never read unless an action explicitly requests it and the user confirmed. |
| N10 | **Maintainability.** Small typed modules, Pydantic models for every boundary, no reflection-based magic. |

## 8. Complete System Architecture

### 8.1 Technology stack (decision)

| Concern | Choice | Alternatives considered | Why |
|---------|--------|------------------------|-----|
| Language | **Python 3.11+** | TypeScript/Node (nut.js), C#/.NET | Best LLM SDK ecosystem, Playwright, mature Windows input/capture libs (`mss`, `pyautogui`, `pywinauto`, `pywin32`), Pydantic for strict schemas, trivial for coding agents to extend. C# has the best native Windows access but a weak agent/LLM ecosystem; Node's desktop-control libraries are less mature on Windows. |
| Packaging | `pyproject.toml`, `src/` layout, `uv` (pip fallback) | poetry, plain requirements.txt | Fast, lockfile, standard. |
| Schemas | **Pydantic v2** | dataclasses + jsonschema | Discriminated unions for the action vocabulary, JSON-schema export for model structured output, validation errors that can be fed back to the model. |
| Screen capture | **`mss`** | `pyautogui.screenshot`, `pywin32` BitBlt, DXGI | Fast, multi-monitor, physical pixels, no extra dependencies. |
| Input | **`pyautogui`** behind an `InputBackend` interface | `pynput`, ctypes `SendInput` | Simplest to start; the interface allows a ctypes `SendInput` backend later for Unicode/robustness. Unicode text is typed via clipboard paste (see Windows section). |
| Window management | **`pywinauto` (uia backend) + `pywin32`** | `pygetwindow`, raw ctypes | Window enumeration, focus, rects, and later the UIA element tree from the same library. |
| OCR (optional) | **RapidOCR (onnxruntime)** behind an `OCRProvider` interface | Tesseract, Windows.Media.Ocr (`winocr`) | pip-installable, no external binary. Tesseract needs a separate installer; winocr depends on WinRT projections that are fragile. Vision LLM remains the primary reader; OCR is an enrichment. |
| Browser | **Playwright (async API, Chromium/Edge channel, dedicated persistent profile)** | Selenium, pixel-driving the user's browser | Deterministic DOM-level actions, accessibility snapshots, persistent login sessions in an isolated profile. |
| LLM access | **`ModelProvider` protocol**; `openai_compat` (OpenAI, Azure, OpenRouter, Ollama, LM Studio, vLLM) + `anthropic` + `fake` | LangChain, LiteLLM | Two small adapters cover almost every provider; no framework lock-in. |
| Config | **TOML file + env overrides via `pydantic-settings`** | YAML, .env only | Typed, comment-friendly, layered. |
| Logging | **`structlog` -> console + JSONL** | stdlib logging only | Structured events are needed for replay and the Control Center. |
| Persistence | **Files: JSON/JSONL/PNG under a run directory**; **SQLite (stdlib)** only for memory in Phase 10 | Postgres, Redis | No infrastructure. SQLite is embedded and justified only when memory needs querying. |
| CLI | **Typer + Rich** | argparse | Approval prompts, tables, colored status with minimal code. |
| Control Center | **FastAPI + WebSocket + static vanilla HTML/JS (no build step)** | Electron, Tauri, PyQt, Textual | Same process, no toolchain, screen preview via JPEG frames over WebSocket. |
| Tests | **pytest** (+ `pytest-asyncio`) | unittest | Standard. |

All package names must be verified on PyPI before installation by the implementing agent; none are to be assumed to exist under a different name.

### 8.2 Architecture overview

```
+---------------------------------------------------------------------------------+
|                                 local-control process                           |
|                                                                                 |
|  CLI / Control Center (FastAPI+WS)                                              |
|          |  goal, approvals, stop            ^ events, screen preview            |
|          v                                   |                                  |
|  +-----------------------------------------------------------------------+      |
|  |                          AgentRunner (the loop)                        |      |
|  |                                                                       |      |
|  |  Observer --> Planner --> SafetyValidator --> ApprovalGate --> Executor|      |
|  |     ^            ^  (LLM via ModelProvider)                     |     |      |
|  |     |            |                                             v     |      |
|  |     +--------- Verifier <---------------- ActionResult <--------+     |      |
|  |                                                                       |      |
|  |  StuckDetector, Budget, RecoveryPolicy, TaskState                     |      |
|  +-----------------------------------------------------------------------+      |
|          |                                   |                                  |
|          v                                   v                                  |
|  Tools: Input, Window, Wait, FileSystem, Terminal, Browser (Playwright)         |
|  Observation: ScreenCapture (mss), WindowManager (pywinauto), OCR (optional)    |
|  Safety: Policy rules, PathRules, CommandRules, KillSwitch                       |
|  Models: openai_compat | anthropic | fake                                       |
|  EventBus --> RunStore (events.jsonl, screenshots/, state.json), AuditLog        |
|  Memory (Phase 10): SQLite                                                      |
+---------------------------------------------------------------------------------+
                     |
                     v
              Windows desktop (Win32 / UIA / PowerShell / Edge)
```

Detailed diagrams and contracts are in [ARCHITECTURE.md](./ARCHITECTURE.md).

### 8.3 Source layout (target)

```
local-control/
  pyproject.toml
  README.md
  config/default_config.toml
  docs/
  src/local_control/
    __init__.py
    __main__.py                 # python -m local_control
    cli.py                      # typer app: run, doctor, replay, config
    config/settings.py          # Settings (pydantic-settings), loader, defaults
    core/
      types.py                  # Observation, ActionProposal, ActionResult, TaskState, Plan, StepRecord, RunStatus
      actions.py                # Typed action vocabulary (discriminated union)
      events.py                 # Event models + EventBus
      errors.py                 # Exception hierarchy
      run_store.py              # Run directory persistence + replay loader
      coordinates.py            # CoordinateMapper (model image space <-> screen)
    agent/
      runner.py                 # AgentRunner: OBSERVE->UNDERSTAND->PLAN->PROPOSE->VALIDATE->EXECUTE->VERIFY->REPLAN
      planner.py                # Prompt assembly, model call, parsing, re-prompt on invalid output
      verifier.py               # Deterministic checks + assessment merge
      recovery.py               # Recovery ladder
      stuck_detector.py
      budget.py
      prompts/                  # system_planner.md, system_verifier.md, action_schema.md (generated)
    observation/
      observer.py               # Composes Observation
      screen.py                 # ScreenCapture (mss), DPI awareness init
      windows.py                # WindowManager (pywinauto/pywin32)
      ocr.py                    # OCRProvider protocol + RapidOCR adapter (Phase 6+)
      uia.py                    # UIA element snapshot (SHOULD, Phase 6/11)
      image.py                  # downscale, encode, perceptual hash, diff
    safety/
      policy.py                 # tier classification per action type + context
      validator.py              # SafetyValidator: schema, bounds, policy, mode -> Verdict
      approval.py               # ApprovalGate protocol + CLI/ControlCenter implementations
      kill_switch.py            # hotkey, corner failsafe, stop file, StopToken
      path_rules.py             # protected/allowed path logic
      command_rules.py          # shell command deny/confirm/allow patterns
    execution/
      executor.py               # dispatch validated action -> tool, wrap ActionResult
      tools/
        base.py                 # Tool protocol
        input_tool.py           # mouse/keyboard via InputBackend
        window_tool.py
        wait_tool.py
        filesystem_tool.py
        terminal_tool.py
        browser_tool.py         # Phase 8
    models/
      provider.py               # ModelProvider protocol, ModelRequest/ModelResponse, usage
      openai_compat.py
      anthropic.py
      fake.py                   # scripted provider for tests
      registry.py               # name -> provider factory
    memory/                     # Phase 10
      store.py
      workflows.py
    control_center/             # Phase 9
      server.py
      static/index.html, app.js, style.css
    observability/
      logging.py
      audit.py
  tests/
    unit/  integration/  desktop/  e2e/  fixtures/
```

## 9. Major Components and Responsibilities

Each component: what, why, inputs, outputs, dependencies, failure modes, communication.

### 9.1 AgentRunner (`agent/runner.py`)

- **What:** Owns the execution loop and the `TaskState`. Drives one iteration: observe -> plan/propose -> validate -> approve -> execute -> verify -> update state -> check budgets/stuck -> repeat or terminate.
- **Why:** A single, readable place where the control flow lives. No hidden orchestration frameworks.
- **Inputs:** `Goal`, `Settings`, injected components (Observer, Planner, Validator, ApprovalGate, Executor, Verifier, EventBus, StopToken).
- **Outputs:** `RunResult` (status, summary, step count, cost), stream of `Event`s.
- **Dependencies:** Everything in `agent/`, `safety/`, `execution/`, `observation/`.
- **Failure modes:** Unhandled tool exception (caught, becomes `ActionResult(success=False)`); budget exhausted (`RunStatus.FAILED_BUDGET`); stop requested (`STOPPED_BY_USER`); provider outage (retry with backoff, then `FAILED_PROVIDER`).
- **Communication:** Direct method calls to components; publishes events to EventBus; polls `StopToken` before and after each blocking call.

### 9.2 Observer (`observation/observer.py`)

- **What:** Builds an `Observation`: screenshot (original + model-scaled), screen geometry, active window, window list, cursor position, timestamp, last `ActionResult`, optional OCR/UIA data.
- **Why:** One consistent, testable snapshot of "the world" per step.
- **Inputs:** `ScreenCapture`, `WindowManager`, optional `OCRProvider`, previous `ActionResult`, observation settings.
- **Outputs:** `Observation` (Pydantic), screenshot files in the run directory.
- **Dependencies:** `mss`, `pywinauto`/`pywin32`, Pillow.
- **Failure modes:** Capture returns black frame (secure desktop / UAC / lock screen / DRM) -> flagged `screen_state="secure_desktop_or_locked"`; window enumeration throws for a closing window -> skipped; DPI mismatch -> caught by `CoordinateMapper` self-check at startup.
- **Communication:** Called by AgentRunner; publishes `ObservationCaptured`.

### 9.3 Planner (`agent/planner.py`)

- **What:** Assembles prompts (system prompt + goal + plan + condensed history + current observation with image), calls `ModelProvider`, parses the response into `PlannerResponse` (assessment of previous action, updated plan, one `ActionProposal`, expected outcome, confidence, rationale). On invalid JSON/schema it re-prompts with the validation error (max 2 retries).
- **Why:** The only probabilistic component; isolating it keeps everything else deterministic.
- **Inputs:** `TaskState`, `Observation`, `Plan`, condensed history, memory hints (Phase 10).
- **Outputs:** `PlannerResponse`.
- **Dependencies:** `models/`, prompts, `core/actions.py` JSON schema.
- **Failure modes:** Malformed output after retries -> `PlannerError` -> recovery ladder; hallucinated coordinates -> caught by validator bounds check; prompt injection from screen -> mitigated by prompt framing + independent validator (see SECURITY_MODEL).
- **Communication:** Called by AgentRunner; publishes `PlannerCalled`, `ProposalReceived`.

### 9.4 SafetyValidator (`safety/validator.py`)

- **What:** Deterministic gate. Checks: schema validity, coordinate bounds, target window sanity, policy tier (`SAFE`/`CONFIRM`/`BLOCKED`) from `policy.py` + `path_rules.py` + `command_rules.py`, current autonomy mode, per-run pre-approvals, rate limits (e.g., max N destructive ops per run). Produces a `Verdict`.
- **Why:** The LLM must never be the last line of defense.
- **Inputs:** `ActionProposal`, `Observation`, `Settings.safety`, `RunPermissions`.
- **Outputs:** `Verdict {allow|needs_confirmation|blocked, tier, reasons[], human_summary}`.
- **Dependencies:** none outside `safety/` and `core/`.
- **Failure modes:** Rule gaps (an action class not covered) -> **default is CONFIRM**, never SAFE. Bugs are caught by table-driven tests.
- **Communication:** Pure function style; AgentRunner passes the verdict to ApprovalGate.

### 9.5 ApprovalGate (`safety/approval.py`)

- **What:** Presents a `needs_confirmation` verdict to the human and returns `approved | denied | approved_for_run(category)`. CLI implementation (Rich prompt) in MVP; Control Center implementation in Phase 9.
- **Why:** Human-in-the-loop for consequential actions.
- **Inputs:** `Verdict`, `ActionProposal`, screenshot thumbnail path.
- **Outputs:** `ApprovalDecision`.
- **Failure modes:** Timeout (configurable, default none -> waits indefinitely, run status `WAITING_APPROVAL`); user denial -> planner is told the action was denied and must choose another path.
- **Communication:** Awaited by AgentRunner; publishes `ApprovalRequested`, `ApprovalDecided`.

### 9.6 Executor (`execution/executor.py`) and Tools (`execution/tools/`)

- **What:** Maps a validated action to the responsible Tool, runs it with a timeout, captures result data and errors into `ActionResult`. Tools are thin adapters: `InputTool`, `WindowTool`, `WaitTool`, `FileSystemTool`, `TerminalTool`, `BrowserTool`.
- **Why:** Separation between "decide" and "do". Tools are swappable and individually testable.
- **Inputs:** `ActionProposal` (validated), `CoordinateMapper`, `StopToken`.
- **Outputs:** `ActionResult {action_id, success, started_at, duration_ms, data, error}`.
- **Dependencies:** `pyautogui`, `pywinauto`, `send2trash`, `subprocess`, Playwright.
- **Failure modes:** Target window closed; input blocked by UIPI (elevated window) -> error `input_blocked_elevated`; PowerShell timeout -> process tree killed; Playwright selector not found -> error with page snapshot.
- **Communication:** Called by AgentRunner; publishes `ActionStarted`, `ActionFinished`.

### 9.7 Verifier (`agent/verifier.py`)

- **What:** Combines (a) deterministic post-conditions from the tool (`focus_window` -> foreground handle matches; `fs_move` -> destination exists; `shell_run` -> exit code), (b) cheap screen-change signals (perceptual hash distance, active window title change) and (c) the planner's next-step `assessment.previous_action_outcome`. Emits `VerificationResult {outcome: success|failure|unknown, evidence}`.
- **Why:** The agent must know when an action did nothing or did the wrong thing.
- **Inputs:** `ActionProposal.expected_outcome`, `ActionResult`, `Observation` before/after, `PlannerResponse.assessment`.
- **Outputs:** `VerificationResult`, drives `RecoveryPolicy`.
- **Failure modes:** False positives (screen changed for unrelated reasons); mitigated by weighting deterministic checks highest. Verification of GUI outcomes relies on the model; cost is zero extra calls in MVP because assessment rides on the next planner call.
- **Communication:** Called by AgentRunner after the next observation.

### 9.8 RecoveryPolicy (`agent/recovery.py`) and StuckDetector, Budget

- **What:** Given `VerificationResult` history and `TaskState`, choose: `continue`, `retry_with_hint`, `replan`, `ask_user`, `abort`. StuckDetector flags repeated identical actions or unchanged screens. Budget enforces max steps, max wall time, max cost, max consecutive failures.
- **Why:** Bounded, predictable failure behavior.
- **Outputs:** `RecoveryDecision` injected as a hint into the next planner prompt, or a terminal `RunStatus`.

### 9.9 ModelProvider (`models/`)

- **What:** `ModelProvider.complete(ModelRequest) -> ModelResponse`. `ModelRequest` carries messages (text + images), a JSON schema for the expected output, temperature, max tokens. Response carries raw text, parsed JSON (if provider supports structured output), token usage, latency.
- **Why:** Provider independence (N6).
- **Failure modes:** Rate limit / 5xx -> exponential backoff (3 attempts); context too large -> history condensation; provider lacks vision -> startup check fails with clear error.

### 9.10 EventBus, RunStore, AuditLog (`core/events.py`, `core/run_store.py`, `observability/`)

- **What:** In-process synchronous pub/sub of typed `Event`s. RunStore writes `events.jsonl`, `screenshots/NNNN.png`, `state.json`. AuditLog is a separate append-only `audit.jsonl` containing only security-relevant events (verdicts, approvals, executed CONFIRM/BLOCKED-tier attempts, kill switch).
- **Why:** Replay, debugging, Control Center feed, accountability.
- **Failure modes:** Disk full -> run aborts safely (logging failure is treated as fatal, not ignored).

### 9.11 KillSwitch (`safety/kill_switch.py`)

- **What:** Sets a global `StopToken` on: global hotkey (default `Ctrl+Alt+Shift+Q`, registered via `pynput`/`RegisterHotKey`), mouse in any screen corner for > 300 ms (pyautogui failsafe semantic, implemented in our own poller so it also works between actions), presence of a stop file, Control Center Stop. Executor checks the token before each action and between typed characters.
- **Why:** The user must always be able to regain control faster than the agent acts.

### 9.12 Control Center (`control_center/`, Phase 9)

- **What:** Local FastAPI app on `127.0.0.1` with WebSocket streaming of events and JPEG preview frames; approve/deny buttons; Stop; goal input; run history browser.
- **Why:** F23. Until Phase 9, the CLI is the interface.

### 9.13 Memory (`memory/`, Phase 10)

- **What:** SQLite store for (a) user preferences, (b) app/UI hints learned ("in app X, settings are under Ctrl+,"), (c) recorded successful runs as parameterizable workflows. Retrieval is keyword/tag based, not vector search, in V3.

## 10. Data Flow

```
Goal ─┐
      v
[TaskState] ──> Observer ──> Observation(screenshot, windows, last_result)
                                     │
                                     v
                     Planner(model) ──> PlannerResponse{assessment, plan, action, expected_outcome, confidence}
                                     │
                                     v
                     Verifier(assessment + last deterministic result) ──> VerificationResult ──> RecoveryPolicy
                                     │
                                     v
                     SafetyValidator(action, observation, mode) ──> Verdict
                                     │
                         ┌───────────┼───────────┐
                      allow   needs_confirmation  blocked
                         │           │             │
                         │      ApprovalGate       └──> feedback to planner ("blocked: <reason>")
                         │        │      │
                         │    approved  denied ──> feedback to planner
                         v        v
                      Executor(tool) ──> ActionResult
                                     │
                                     v
                     TaskState.append(StepRecord) ; Budget/Stuck checks ; loop
```

All boxes publish events. Exact payloads are specified in ARCHITECTURE.md section "Contracts".

## 11. Agent Execution Loop

```
run(goal):
  state = TaskState.new(goal, mode)
  emit RunStarted
  obs = observer.observe(last_result=None)
  while True:
    check_stop()
    response = planner.propose(state, obs)                  # UNDERSTAND + PLAN + PROPOSE (one model call)
    verification = verifier.verify(state.last_step, obs, response.assessment)   # VERIFY previous step
    state.record_verification(verification)
    decision = recovery.decide(state, verification)         # REPLAN / ASK / ABORT / CONTINUE
    if decision.terminal: break
    if decision.kind == "replan": state.plan_hint = decision.hint ; continue (no action this iteration)
    action = response.action
    if action is Done: if verifier.goal_check(state, obs, response): break else: state.plan_hint="not verified"; continue
    if action is AskUser: answer = approval_gate.ask(action.question); state.add_user_answer(answer); obs = observer.observe(); continue
    verdict = validator.validate(action, obs, state.permissions)
    if verdict.blocked: state.feedback("blocked", verdict); continue
    if verdict.needs_confirmation:
        approval = approval_gate.request(verdict, action)
        if denied: state.feedback("denied", verdict); continue
        if approved_for_run: state.permissions.grant(category)
    result = executor.execute(action)                        # EXECUTE
    wait(action.settle_ms or default)                        # let UI settle
    obs = observer.observe(last_result=result)               # OBSERVE
    state.append_step(obs_before, response, verdict, result)
    if budget.exceeded(state) or stuck.detected(state): decide terminal/replan
  emit RunFinished(state.status, summary)
```

Key properties: exactly one action per iteration; verification of step *k* occurs at the start of iteration *k+1* using the fresh observation; `Done` is itself verified; every branch produces feedback for the planner rather than silently looping.

## 12. Observation -> Reasoning -> Action -> Verification Cycle

| Stage | Who | Deterministic? | Artifact |
|-------|-----|----------------|----------|
| OBSERVE | Observer | yes | `Observation` |
| UNDERSTAND | Planner (model) | no | `assessment.screen_summary`, `previous_action_outcome` |
| PLAN | Planner (model) | no | `Plan {steps[], current_index}` |
| PROPOSE | Planner (model) | no | `ActionProposal` |
| VALIDATE | SafetyValidator | yes | `Verdict` |
| APPROVE | Human via ApprovalGate | human | `ApprovalDecision` |
| EXECUTE | Executor + Tool | yes | `ActionResult` |
| VERIFY | Verifier (deterministic + model assessment) | mixed | `VerificationResult` |
| REPLAN | RecoveryPolicy + Planner | mixed | updated `Plan`, hints |

## 13. Tool/Action Abstraction

The action vocabulary is a **closed, typed, discriminated union** (`core/actions.py`). The model may only emit these; anything else is a schema error.

Common envelope fields on every action: `type`, `target_description` (human readable, used in approval prompts and logs), `expected_outcome` (what should be observable after), `confidence` (0-1), `rationale` (short), `settle_ms` (optional wait after execution).

| Group | Actions (MVP unless noted) | Default tier |
|-------|----------------------------|--------------|
| Mouse | `click {x,y,button,clicks}`, `move_mouse {x,y}`, `drag {from,to,button}`, `scroll {x,y,dx,dy}` | SAFE (context rules may raise, e.g., clicking on a detected "Pay"/"Delete" control -> CONFIRM) |
| Keyboard | `type_text {text}`, `press_keys {keys[]}` | SAFE, except dangerous hotkeys (`Win+R`, `Alt+F4` on unknown window, `Ctrl+Shift+Esc`) -> CONFIRM; `type_text` into a password field -> BLOCKED |
| Window | `focus_window {handle}`, `list_windows`, `close_window {handle}` | SAFE / SAFE / CONFIRM |
| Control | `wait {seconds}`, `done {summary}`, `fail {reason}`, `ask_user {question}` | SAFE |
| Observation | `ocr_region {rect}` (SHOULD), `read_ui_tree {handle}` (SHOULD) | SAFE |
| Filesystem | `fs_list`, `fs_read`, `fs_stat`, `fs_mkdir`, `fs_write`, `fs_copy`, `fs_move`, `fs_delete` (to Recycle Bin) | list/read/stat SAFE inside allowed roots; mkdir/write/copy SAFE inside allowed roots for new files, CONFIRM when overwriting; move/delete CONFIRM; anything touching protected paths BLOCKED; permanent delete does not exist as an action |
| Terminal | `shell_run {command, cwd, timeout_s}` | CONFIRM by default; allowlisted read-only commands SAFE; denylisted patterns BLOCKED |
| Browser (Phase 8) | `browser_navigate`, `browser_click {ref}`, `browser_type {ref,text}`, `browser_read`, `browser_snapshot`, `browser_back`, `browser_tabs`, `browser_download` | navigate/read/snapshot SAFE; click/type SAFE unless page context matches payment/credential/send patterns -> CONFIRM or BLOCKED; download CONFIRM |

Coordinates in mouse actions are expressed in **model image space** (the downscaled screenshot). `CoordinateMapper` converts to physical screen coordinates. This is a hard rule so the model never has to reason about DPI.

## 14. State Management

- `TaskState` (in memory, serialized to `state.json` after every step): goal, run_id, status, autonomy mode, `RunPermissions` (per-run grants), `Plan`, `steps: list[StepRecord]`, `user_answers`, `feedback_queue` (blocked/denied/verification hints for the next prompt), counters (steps, failures, consecutive failures, tokens, cost), timestamps.
- `StepRecord`: observation reference (screenshot path + metadata, not pixels), `PlannerResponse`, `Verdict`, `ApprovalDecision?`, `ActionResult`, `VerificationResult?`.
- **History condensation** for the prompt: last N (default 6) steps in full text, older steps summarized as one line each; only the current screenshot is sent as an image (optionally the previous one for diff context, configurable).
- Single run at a time per process. No shared mutable global state except `StopToken`.

## 15. Task Lifecycle

```
CREATED -> RUNNING <-> WAITING_APPROVAL
              |   <-> WAITING_USER
              |-> COMPLETED          (Done verified)
              |-> FAILED_UNVERIFIED  (Done not confirmed after retries)
              |-> FAILED_BUDGET      (steps/time/cost)
              |-> FAILED_STUCK
              |-> FAILED_PROVIDER
              |-> FAILED_ERROR       (unexpected exception, logged)
              |-> ABORTED_BY_AGENT   (planner emitted fail{reason})
              |-> STOPPED_BY_USER    (kill switch)
```

Every terminal state writes a `summary.md` into the run directory with: goal, outcome, steps taken, files touched, commands run, approvals granted, open issues.

## 16. Error Handling

- **Errors are data.** Tool errors become `ActionResult(success=False, error={code, message, recoverable})`; they are never raised across the runner boundary.
- **Error codes are an enum** (`core/errors.py`): `target_not_found`, `window_gone`, `input_blocked_elevated`, `secure_desktop`, `timeout`, `path_not_allowed`, `command_blocked`, `provider_error`, `schema_invalid`, `browser_selector_not_found`, `unknown`.
- The planner receives a compact rendering of the error in the next prompt.
- Unexpected exceptions in the runner itself -> `FAILED_ERROR`, full traceback to `events.jsonl`, never a silent crash.

## 17. Recovery Strategy

Escalation ladder, applied by `RecoveryPolicy`:

1. **Continue** - verification success or unknown-but-progressing.
2. **Retry with hint** (max 2 per step) - same intent, planner told what failed ("click at (412,300) changed nothing; the element may be at a different position or require double click").
3. **Replan** - after 2 failed retries or when the planner's assessment says the screen contradicts the plan. Planner must emit a revised `Plan`.
4. **Ask user** - after a failed replan on the same step, or whenever the planner has confidence < 0.3 for two consecutive proposals, or a BLOCKED action is the only way forward.
5. **Abort** - budget exhausted, consecutive failures >= 3 after asking, or the user answers "stop".

Stuck detection: 3 identical actions in a row, or perceptual hash of the screen unchanged over 3 actions that expected change -> force step 3 (replan).

## 18. Permission/Approval System (summary)

Three tiers: **SAFE** (auto), **CONFIRM** (human y/n with exact description), **BLOCKED** (never executed by the agent; the user is asked to perform it manually). Three autonomy modes: `step` (approve every action, default for first runs), `assisted` (default; SAFE auto, CONFIRM asks), `trusted` (as assisted, plus the user may grant per-run category pre-approvals such as "file moves within Downloads"). **No mode changes BLOCKED.** Full detail in [SECURITY_MODEL.md](./SECURITY_MODEL.md).

## 19. Security Model (summary)

- LLM output is untrusted; screen content, OCR text, web pages and file contents are untrusted (prompt injection surface).
- The validator is independent of the model and is the only path to execution.
- Agent runs as a **standard (non-admin) user**; it never elevates. UAC prompts are detected and handed to the human.
- The agent never types credentials; it yields to the user for logins.
- Browser uses a dedicated Playwright profile, not the user's daily profile.
- Append-only audit log; kill switch; protected path and command deny lists.
- Windows offers no cheap sandbox; the model is compensating controls, plus the recommendation to develop and demo inside a VM or Windows Sandbox.

## 20. Memory Architecture (Phase 10, FUTURE)

- **Episodic:** run directories already persist everything; memory indexes them.
- **Semantic (hints):** table `hints(app, key, value, confidence, source_run_id, created_at)`.
- **Preferences:** table `preferences(key, value)`, e.g., default Downloads categorization scheme.
- **Workflows:** table `workflows(name, goal_template, steps_json, params_json, success_count)`; a workflow is a sanitized recording of a successful run's plan and actions with variables extracted; replay still goes through validator/approval every time.
- Retrieval: tag/keyword match on app name and goal terms; injected into the planner prompt as a short "Known hints" section (max ~500 tokens). No embeddings in V3.

## 21. Model Abstraction

- `ModelProvider` protocol with `complete()`, `supports_vision`, `supports_json_schema`, `name`.
- `ModelRequest`: `system`, `messages[{role, content: [text|image_png_bytes]}]`, `response_schema` (JSON schema), `temperature`, `max_tokens`.
- `ModelResponse`: `text`, `parsed: dict|None`, `usage {input_tokens, output_tokens, cost_usd?}`, `latency_ms`, `provider`, `model`.
- Structured output strategy: if the provider supports JSON schema mode, use it; otherwise instruct JSON-only output and parse leniently (strip code fences), then validate with Pydantic. Retry with error feedback up to 2 times.
- Roles (`planner`, `verifier`, `summarizer`) are configuration keys that each map to a provider+model. In MVP all roles map to the same provider; FUTURE may use a cheaper model for summarization or a different one for verification.

## 22. Configuration Strategy

- `config/default_config.toml` shipped in repo; user config at `%LOCALAPPDATA%\\local-control\\config.toml`; env vars `LOCAL_CONTROL__SECTION__KEY` override; CLI flags override env.
- Secrets (API keys) via env vars or Windows Credential Manager through `keyring`; never in TOML committed to git.
- Sections: `[models.<role>]`, `[observation]` (max image width, monitors, ocr enabled), `[safety]` (mode, allowed_roots, extra protected paths, hotkey, corner failsafe), `[budget]`, `[terminal]` (shell path, timeout, allowlist additions), `[browser]` (channel, profile dir), `[control_center]` (host, port), `[logging]`.
- `local-control doctor` validates config, DPI awareness, screen capture, input permission, provider connectivity and vision support.

## 23. Logging/Observability

- structlog JSON to `runs/<run_id>/events.jsonl`, human-readable to console.
- Event types: `RunStarted`, `ObservationCaptured`, `PlannerCalled`, `ProposalReceived`, `VerdictIssued`, `ApprovalRequested`, `ApprovalDecided`, `ActionStarted`, `ActionFinished`, `VerificationResult`, `RecoveryDecision`, `BudgetWarning`, `StopRequested`, `RunFinished`, `Error`.
- `local-control replay <run_id>` prints the timeline and opens screenshots side by side (Rich in CLI; Control Center in Phase 9).
- Sensitive text redaction: `type_text` payloads are logged in full only when the target is not a password field (which is BLOCKED anyway); shell command output is truncated to 8 KB in logs.

## 24. Testing Strategy (summary)

- Pure logic (validator, policy, planner parsing, coordinate mapping, recovery, stuck detection, run store) - unit tests, run anywhere.
- Agent loop - integration tests with `FakeModelProvider` (scripted responses) and `FakeComputer` (in-memory windows + synthetic screenshots).
- Desktop tests (`@pytest.mark.desktop`) against a stdlib Tkinter **test target app** with known widgets - run on a real interactive Windows session only.
- Browser tests against local static HTML fixtures with Playwright.
- Safety tests: table-driven tier tests, prompt-injection corpus, path/command rule fuzzing.
- E2E scenarios in [TEST_PLAN.md](./TEST_PLAN.md).

## 25. Performance Considerations

- Screenshot capture via `mss` ~10-30 ms; downscale + PNG encode ~50-100 ms; window enumeration ~20-50 ms. Budget < 300 ms total.
- Model latency dominates (2-8 s). One call per step. Optional "fast path": if the previous action was a `wait` or `focus_window` with a deterministic success, skip the assessment by reusing the screenshot hash? **No** - keep it simple in MVP; every step gets a fresh observation and one call.
- History condensation keeps prompt growth linear-with-cap.
- OCR (when enabled) ~200-800 ms; run only on request (`ocr_region`) or when configured.
- Control Center preview: JPEG quality 60, max 2 fps, only while a run is active.

## 26. Windows-Specific Considerations

| Topic | Issue | Decision |
|-------|-------|----------|
| DPI scaling | Mixed logical/physical coordinates break clicks | Set `PROCESS_PER_MONITOR_DPI_AWARE_V2` via ctypes at startup, before any capture; `mss` gives physical pixels; `CoordinateMapper` is the single conversion point; `doctor` performs a self-test (move cursor to known pixel, read back). |
| Multi-monitor | Negative coordinates, differing scale factors | MVP: primary monitor only, explicitly documented. Phase 11: virtual-screen coordinates with per-monitor capture. |
| UIPI / elevated windows | A non-elevated process cannot send input to elevated windows | Agent never runs elevated. Detect via `ActionResult.error=input_blocked_elevated` (input sent but no effect + window is elevated) and ask the user. |
| UAC / secure desktop / lock screen | Screenshot is black or stale; input is impossible | Observer flags `screen_state`; runner pauses and asks the user to handle it. |
| SetForegroundWindow restrictions | Focus stealing is limited | Use `pywinauto.set_focus()` which applies the Alt-key workaround; fall back to `ShowWindow`+`SetForegroundWindow`; verify via `GetForegroundWindow`. |
| Unicode typing | `pyautogui.typewrite` fails for non-ASCII and layout-dependent keys | `type_text` copies text to the clipboard, sends Ctrl+V, restores the previous clipboard; fallback `pyautogui.write` for ASCII only; ctypes `SendInput` with `KEYEVENTF_UNICODE` as later backend. |
| Keyboard layouts | Hotkeys depend on layout | Use virtual-key based hotkeys via `pyautogui.hotkey`; document limitation. |
| Fullscreen/DirectX apps | Capture may fail, input may be blocked | Out of scope; observer flags black frame. |
| Recycle Bin | Safe deletion | `send2trash`; no permanent delete action exists. |
| PowerShell | Execution policy, profiles, encoding | Run `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command` only for the specific command string; UTF-8 output encoding forced; `pwsh` preferred if present. Bypass applies only to the agent's own child process, not the system policy. |
| Long paths | > 260 chars | Use `\\\\?\\` prefix helper in `path_rules`. |
| SmartScreen / Defender | Downloaded executables trigger dialogs | Running downloaded executables is BLOCKED. |
| Antivirus flagging | Input simulation libraries may be flagged | Document; no workaround. |
| RDP / session 0 | Services and disconnected RDP sessions have no interactive desktop | Agent must run in an interactive logged-in session; `doctor` checks. |
| Sleep / screensaver | Screen turns off mid-task | `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` during runs. |

## 27. Future Extensibility

- New tools = new action types in the union + a Tool + policy rules + tests. No changes to the runner.
- New providers = one adapter in `models/`.
- New approval UIs = one `ApprovalGate` implementation.
- Observation enrichments (UIA tree, Set-of-Marks labels, OCR) are additive fields on `Observation`.
- Separate planner/executor/verifier models are a configuration change once roles exist (F27).
- macOS/Linux would require new `ScreenCapture`, `WindowManager`, `InputBackend`, `TerminalTool` shell adapter implementations; the interfaces are already OS-neutral.

## 28. MVP Definition

See section "MVP Definition" at the end. In short: Phases 0-7 of the implementation plan; CLI only; primary monitor; GUI + filesystem + terminal tools; full safety tiers and approvals; verification and recovery; run persistence and replay; OpenAI-compatible and fake providers.

## 29. Roadmap V1-V4

- **V1 (MVP, Phases 0-7):** Everything above. Demo scenarios 1, 3 and 5 from TEST_PLAN pass in `assisted` mode.
- **V2 (Phases 8-9):** Browser tool (Playwright), Control Center, OCR + UIA enrichment, multi-monitor. Scenarios 2 and 4 pass.
- **V3 (Phase 10):** Memory, hints, reusable workflows, role-specific models, Set-of-Marks targeting.
- **V4 (Phase 11+):** Hardening: `SendInput` backend, workflow marketplace-style import/export, scheduled runs, optional local vision model for cheap verification, macOS exploration.

## 30. Technical Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Vision model coordinate accuracy is insufficient for small UI targets | High | High | Downscale no smaller than 1280 px; allow `zoom_region` re-observation (Phase 6); UIA element targeting (SHOULD); double-verification before destructive clicks. |
| R2 | DPI/coordinate mismatch causes misclicks | Medium | High | Single `CoordinateMapper`, startup self-test, desktop tests. |
| R3 | Prompt injection via screen/web content | High | High | Validator independence, tiering, prompt framing, injection corpus tests, BLOCKED classes. |
| R4 | Model emits invalid JSON frequently | Medium | Medium | JSON-schema mode where supported, lenient parse, 2 retries with errors, fallback provider. |
| R5 | Agent loops without progress | High | Medium | StuckDetector, budgets, recovery ladder. |
| R6 | `pyautogui` limitations (Unicode, elevated windows, speed) | Medium | Medium | Clipboard typing, `InputBackend` interface for `SendInput` later. |
| R7 | Playwright profile locks / Edge already running | Medium | Low | Dedicated profile dir; never attach to the user's running browser. |
| R8 | Cost blow-up on long tasks | Medium | Medium | Cost budget, history condensation, downscaled images. |
| R9 | CI cannot run desktop tests (GitLab shared runners are headless/non-interactive) | Certain | Medium | Split test tiers; desktop tests on a local Windows session or a self-hosted runner with auto-logon. |
| R10 | Antivirus/EDR blocks input simulation or hotkey registration | Low | High | Document; `doctor` detects. |
| R11 | Users run the agent elevated "to make it work" | Medium | High | Refuse to start when elevated unless `--i-understand-elevated` is passed; log loudly. |

## 31. Open Questions

1. **Primary model provider for development.** The architecture is provider-neutral, but the first end-to-end runs need a concrete vision-capable model and API key. Recommendation: any OpenAI-compatible vision endpoint for Phase 3; decision deferred to the user (not an architecture blocker).
2. **Should `shell_run` allow `pwsh`/`cmd` selection per call?** Recommendation: no; one configured shell (PowerShell), `cmd` commands can be invoked via `cmd /c` if truly needed and are subject to the same rules.
3. **OCR necessity.** Modern vision models read text well; OCR may be dropped entirely if Phase 3 experiments show adequate accuracy. Keep the interface, defer the adapter.
4. **Control Center authentication.** Bound to localhost; is a per-run token in the URL needed against other local processes? Recommendation: yes, cheap to add in Phase 9.
5. **Multi-monitor in MVP?** No. Explicit non-goal until Phase 11.

## 32. Explicit Architectural Decisions

See "Critical Decisions" at the end; they are enumerated once to avoid duplication.

---

## Final Recommended Architecture

A single Python 3.11+ process. An `AgentRunner` drives a strict loop: `Observer` builds a typed `Observation` (mss screenshot with DPI-aware geometry, pywinauto window list, last action result); the `Planner` sends it with the goal, the current `Plan` and condensed history to a vision LLM through a `ModelProvider` abstraction (OpenAI-compatible and Anthropic adapters, plus a scripted fake) and receives a strictly validated `PlannerResponse` containing an assessment of the previous action, an updated plan and exactly one typed `ActionProposal` from a closed action vocabulary; the `Verifier` merges that assessment with deterministic tool post-conditions and screen-change signals; the `RecoveryPolicy` decides continue/retry/replan/ask/abort; the deterministic `SafetyValidator` classifies the proposal as SAFE/CONFIRM/BLOCKED using policy, path and command rules plus the autonomy mode; the `ApprovalGate` obtains human consent for CONFIRM; the `Executor` dispatches to thin tools (input, window, wait, filesystem, terminal, later browser via Playwright); every stage emits typed events to an in-process `EventBus` persisted as JSONL and screenshots for replay, with a separate audit log; a `KillSwitch` (hotkey, corner, stop file, UI) can halt execution at any point. Interface is a Typer CLI in the MVP and a localhost FastAPI + WebSocket Control Center in V2. Memory (SQLite) and reusable workflows arrive in V3. No database servers, brokers or cloud infrastructure.

## Implementation Order

1. Repository foundation: `pyproject.toml`, `src/` layout, settings loader, structlog, EventBus, RunStore, typed core models and the action union, error enum, Typer CLI skeleton with `doctor`, CI running unit tests on Linux.
2. Observation: DPI awareness init, `ScreenCapture`, `WindowManager`, `CoordinateMapper`, image utilities, `Observer`; `doctor` capture self-test; desktop test target app.
3. Minimal safety primitives that must exist before any input is sent: `StopToken`, `KillSwitch`, `step` autonomy mode with CLI approval of every action.
4. Actions: `InputBackend` (pyautogui) + `InputTool`, `WindowTool`, `WaitTool`, `Executor`, `ActionResult`; CLI `act` command for manual single actions.
5. Model layer: `ModelProvider`, `openai_compat`, `fake`, registry, structured output + retry.
6. Agent loop v1: `Planner` (single prompt, reactive), `AgentRunner`, `TaskState`, budgets, `done/fail/ask_user`, run persistence, `replay`. First goals achieved in `step` mode.
7. Planning: explicit `Plan` in the response schema, replanning triggers, history condensation.
8. Full safety: `policy.py` tiers, `path_rules`, `command_rules`, `SafetyValidator`, `assisted`/`trusted` modes, per-run grants, audit log.
9. Verification and recovery: `Verifier` deterministic checks + screen hash, `RecoveryPolicy`, `StuckDetector`, `zoom_region`, injection-aware prompt framing tests.
10. Filesystem and terminal tools with rules and Recycle Bin.
11. **MVP complete.** Browser tool (Playwright).
12. Control Center.
13. Memory and workflows.
14. Hardening: `SendInput` backend, multi-monitor, UIA/Set-of-Marks, OCR adapter, cost optimizations.

## MVP Definition

The MVP is done when, on a standard Windows 11 machine at 100% or 150% DPI with one monitor, a user can run `local-control run "<goal>"` in `assisted` mode and the agent:

1. captures the screen and windows, calls a configured vision model through the provider abstraction, and performs GUI actions (click, type, hotkeys, scroll, drag, focus window), filesystem operations inside allowed roots and gated PowerShell commands;
2. emits only typed actions from the closed vocabulary; every action passes the `SafetyValidator`; CONFIRM actions prompt in the CLI with an exact description; BLOCKED actions are never executed;
3. verifies each step, detects failures and stuck loops, retries, replans, asks the user, or aborts within configured budgets;
4. can be stopped instantly via hotkey, corner failsafe or stop file;
5. persists the full run and can replay it;
6. passes TEST_PLAN scenarios **1 (Organize Downloads)**, **3 (Clone a repository and diagnose a failure)** and **5 (Rename and categorize project files)** end-to-end, and all unit/integration/safety suites are green in CI.

Explicitly *not* in the MVP: browser tool, Control Center, OCR, UIA tree, memory, workflows, multi-monitor, Anthropic adapter (optional if trivial), separate verifier model.

## Future Roadmap

After the MVP is stable: Playwright browser tool (Phase 8); Control Center with live preview and approvals (Phase 9); SQLite memory, hints and reusable workflows (Phase 10); hardening with `SendInput` backend, multi-monitor, UIA and Set-of-Marks targeting, OCR adapter, role-specific models, cost optimizations (Phase 11); later exploration of scheduled runs and non-Windows platforms.

## Critical Decisions

1. **Python 3.11+ single process** over TypeScript or C#: strongest ecosystem for LLM SDKs, Playwright, Windows capture/input; easiest for a coding agent to extend.
2. **Closed, typed action vocabulary (Pydantic discriminated union)**; the LLM never emits code. This is the foundation of safety, auditability and testability.
3. **Deterministic SafetyValidator is the only path to execution**, independent of the model, with a default tier of CONFIRM for anything unclassified and an immutable BLOCKED class.
4. **One model call per step** carrying assessment + plan + action; verification of step k happens in the planner call for step k+1, merged with deterministic checks. Avoids doubling cost/latency while still detecting failures. A separate verifier model is a later configuration option, not a redesign.
5. **Coordinates live in model image space**; `CoordinateMapper` is the single conversion point; DPI awareness is set at process start. Prevents the most common class of misclicks.
6. **Agent never runs elevated and never types credentials**; UAC, logins and payments are handed to the human. Compensates for the absence of a practical Windows sandbox.
7. **Filesystem deletion only to Recycle Bin; no permanent delete action exists.** Reversibility by construction.
8. **PowerShell as the single shell**, non-interactive, timeout-bound, rule-gated; CONFIRM by default with a small SAFE allowlist and a BLOCKED denylist.
9. **Playwright with a dedicated persistent profile** for browser work, never attaching to the user's daily browser; DOM-level typed actions instead of pixel-driving the browser.
10. **Files (JSONL/PNG) for run persistence, SQLite only for memory later**; no external database or broker.
11. **CLI first, Control Center second**; the Control Center is a FastAPI + WebSocket app in the same process with static HTML/JS, no frontend build chain.
12. **Primary monitor only in MVP**; multi-monitor is a hardening item, stated as a known limitation rather than half-implemented.
13. **Minimal safety (kill switch + step mode) is built before the first input action**, not after the agent loop, deviating from the suggested phase order for safety reasons.
14. **Provider abstraction with two adapters (OpenAI-compatible, Anthropic) plus a fake**; no LangChain/LiteLLM to avoid dependency weight and hidden prompt manipulation.
15. **OCR and UIA are optional enrichments behind interfaces**, deferred until experiments show the vision model alone is insufficient.
