# V1 → V2: what changed and why

This rewrite came out of a review of the original `Copilot-CLI-Harness`. The notes
below double as that review: each item is something V1 got wrong or could improve,
and how V2 addresses it.

## 1. The feature backlog moved from JSON to GitHub Issues (headline change)

**V1:** `feature_list.json` was the source of truth. The initializer also created an
Epic issue and was told to mirror progress onto it — so there were *two* trackers
that drifted. Worse, the initializer prompt wrote the 200-item JSON in a **7-stage
append dance** ("writing 200+ tests at once often fails due to JSON formatting
errors").

**V2:** Each feature is a GitHub issue (`feature` label + a category label), created
as a sub-issue of one Epic. **Closing the issue = verified.** Progress is computed
from issue state (`github_backend.summarize_issues`). One source of truth, no fragile
bulk-JSON, and the Projects board / timeline / human edits come for free.

## 2. Replaced subprocess stdout-scraping with the Copilot SDK

**V1:** `copilot_client.py` ran `copilot -p ...` and reconstructed "messages" by
string-matching stdout (`"✓"`, `"Create"`, `[Tool: X]`). It hand-rolled
`AssistantMessage`/`ToolUseBlock`/`ToolResultBlock` dataclasses to imitate an SDK that
did not exist yet. Error detection literally did `if "error" in text.lower()`, so a
model saying "no errors found" counted as an error.

**V2:** `sdk_client.py` uses `github-copilot-sdk` (GA, June 2026). Real streamed
events, real session lifecycle, structured results. ~200 lines of brittle parsing
deleted.

## 3. The security allowlist is now actually enforced

**V1 (the most serious finding):** `security.py` defined a `bash_security_hook` and
`create_client()` attached it to a `CopilotClientOptions.hooks` field — but
`receive_response()` built its own `copilot ... --allow-all-tools --allow-all-paths`
command and **never passed the hook to the subprocess.** The hook was dead code. The
README nonetheless claimed `rm`/`curl`/`sudo` were blocked. They were not — the agent
ran fully unrestricted.

**V2:** The allowlist logic is pure (`security.evaluate_command`) and is wired into
the SDK's `on_permission_request` handler in `sdk_client.make_permission_handler`, so
every shell command is actually checked. It has unit tests (V1 had none). Added `gh`
to the allowlist because the Issues workflow needs it. `--permission-mode approve-all`
is an explicit opt-out instead of the silent default.

## 4. Fixed configuration drift

**V1:** the default model was declared three different ways — `claude-opus-4.5` in
`autonomous_agent_demo.py`, `claude-sonnet-4.5` in `copilot_client.py`, and
`claude-sonnet-4.5` in the README table. The CLI flag was `--spec` in code but
documented as `--spec-file`, so the documented command failed.

**V2:** one `config.DEFAULT_MODEL`, imported everywhere. The flag is `--spec-file`
in both code and docs.

## 5. Removed dead / no-op code

**V1:** `IntegratedMonitor` was instantiated and "started", but `start()` did nothing
and its tail loop `pass`-ed on every line. The external-terminal monitor was
macOS/Linux-only and fiddly.

**V2:** `monitor.py` is lean and structured — it consumes the SDK's results and a
stream sink instead of regex-scraping, and logs JSON + text per run.

## 6. Real Python packaging

**V1:** loose scripts, no `pyproject.toml`/`requirements.txt`, no pinned deps, a
broken `assets/demo.gif` reference in the README (the file did not exist).

**V2:** a `copilot_harness` package with `pyproject.toml`, a `copilot-harness` console
entry point, declared dependency on the SDK, and `pytest` config. README claims match
reality.

## 7. Harness no longer depends on `gh`

The orchestrator reads progress through the GitHub REST API with the standard library
(`github_backend.GitHubBackend`), using the same token precedence as the SDK
(`COPILOT_GITHUB_TOKEN` → `GH_TOKEN` → `GITHUB_TOKEN`). The agent still uses `gh`
inside its sessions, but the harness itself stays dependency-light and testable.

---

## Ideas intentionally left for later

- **Projects v2 board:** the issues already carry status via open/closed + category
  labels; a GraphQL-backed board with a real Status field is a natural next step.
- **Cost/usage tracking:** the SDK exposes token usage per session — worth surfacing
  in the monitor and as an Epic comment.
- **Parallel feature agents:** independent `core` features could be built concurrently
  in separate worktrees, each closing its own issue.
- **Resumable sessions:** the SDK supports session persistence; the loop currently
  starts each session fresh by design, but recovery could reattach.
