# Architecture and Technology Decisions

This document records the architectural and technology stack decisions for `local-control`. Any dependency or pattern chosen outside the core stack defined in [MASTER_PLAN.md](./MASTER_PLAN.md) Section 8.1 must be justified and recorded here.

---

## 1. Core Technology Stack (Phase 0)

| Concern | Choice | Justification |
| :--- | :--- | :--- |
| **Language** | Python 3.11+ | Optimal ecosystem for LLM SDKs, Playwright, Windows automation libraries (`mss`, `pyautogui`, `pywinauto`, `pywin32`), and rapid iteration by AI coding agents. |
| **Packaging** | `pyproject.toml`, `src/` layout, `hatchling`, `uv` lockfile | Modern Python packaging standard with fast dependency resolution and deterministic lockfiles. |
| **Data Contracts** | Pydantic v2 | High-performance parsing, discriminated unions for typed action vocabulary, strict validation, and native JSON schema export for LLM structured output. |
| **CLI Framework** | Typer + Rich | Expressive, type-annotated command line interface with beautiful terminal rendering for approval gates and diagnostic tables. |
| **Logging** | `structlog` | Structured event streaming to console and JSONL formats, essential for deterministic run replay and live telemetry. |
| **Testing** | `pytest` + `pytest-asyncio` | Standard async testing framework with explicit markers (`unit`, `integration`, `desktop`, `browser`, `e2e`) to decouple headless Linux CI from Windows interactive desktop tests. |

---

## 2. Image Hashing (Phase 1 Decision)

- **Choice:** In-house 64-bit difference hash (`dHash`) implemented via `Pillow`.
- **Alternatives Considered:** `imagehash` package.
- **Rationale:** An in-house difference hash requires fewer than 25 lines of Python using standard `Pillow` methods, avoiding the heavy `scipy`/`numpy` dependency tree that `imagehash` pulls in. This keeps the installation footprint minimal and installation fast.

---

## 3. Model Provider Client (Phase 3 Decision)

- **Choice:** Direct `httpx.AsyncClient` HTTP client for OpenAI-compatible and Anthropic endpoints.
- **Alternatives Considered:** `openai` and `anthropic` official SDKs, `LiteLLM`, `LangChain`.
- **Rationale:** A thin raw `httpx` adapter provides total control over timeout, exponential backoff, request tracing, and payload construction without multi-megabyte third-party abstraction layers.

---

## 4. Clipboard Strategy for Unicode Typing (Phase 2 Decision)

- **Choice:** `pywin32` clipboard integration with clipboard state restoration.
- **Alternatives Considered:** `pyperclip`.
- **Rationale:** `pywin32` is already required for Windows window management (`GetForegroundWindow`, `SetForegroundWindow`), eliminating the need for an additional third-party dependency.

---

## 5. Shell Execution Safety (Phase 7 Decision)

- **Choice:** Non-interactive child PowerShell (`pwsh.exe` or `powershell.exe`) with closed stdin, process tree termination on timeout, and environment variable sanitization.
- **Rationale:** Eliminates shell injection and hangs from interactive prompts while stripping API keys and secrets from the child process environment.
