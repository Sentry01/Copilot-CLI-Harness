# Review: Copilot CLI Harness (legacy v1)

Reviewed at commit `4292c5b` on 2026-10-04 against **GitHub Copilot CLI 1.0.91** and
**`github-copilot-sdk` 1.0.16 (Python)**. The v1 code now lives in [`legacy/`](../legacy/).
This document covers what v1 was, whether it still works with current Copilot, and every
flaw found. The replacement design is described in [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. What the repository is

A Python port of Anthropic's `claude-quickstarts/autonomous-coding` "long-running agent"
demo, re-targeted at GitHub Copilot CLI:

1. **Initializer session.** It reads `app_spec.txt` and writes `feature_list.json`, a list of
   about 200 *prose* test cases (`description`, `steps`, `"passes": false`). It also writes
   `init.sh`, creates a private GitHub repo and an "Epic" issue, and scaffolds the app.
2. **Coding sessions, in a loop.** Each session runs `copilot -p <prompt> --allow-all-tools
   --allow-all-paths`. The agent picks a failing entry, implements it, "verifies" it through
   Playwright MCP, and **flips `"passes": true` itself**.
3. **Completion.** The harness counts `"passes": true` entries and stops at 100%.

Supporting pieces: a regex log monitor (`monitor.py`, `monitor_agent.sh`), a Bash allowlist
(`security.py`), a prerequisites script, a set of "superpowers" skills under `.copilot/skills/`,
and custom agents under `.github/agents/`.

## 2. Does it still work with current Copilot?

**Partially. It will probably still launch, but it was never doing what the README claims, and
it ignores almost everything the current CLI and SDK provide.**

| Area | v1 assumption | Current reality (CLI 1.0.91 / SDK 1.0.16) |
|---|---|---|
| Invocation | `copilot -p … --allow-all-tools --allow-all-paths --add-dir` | Still valid flags, so the loop likely still starts. |
| Output | Scrapes human-readable stdout with regex | `--output-format json` (JSONL) exists. The **SDK** emits typed events (`ToolExecutionStartData`, `AssistantUsageData`, `SessionErrorData`, …). |
| Permissions | Python allowlist (never wired in, see C1) | CLI: `--allow-tool` / `--deny-tool` with `shell(cmd:*)`, `write(path)`, `url(...)`, `<mcp>(tool)`. **Deny now wins over `--allow-all-tools`.** SDK: `on_permission_request` gets a *parsed* shell request (`commands[].identifier`, `read_only`, `possible_paths`, `possible_urls`). |
| Hooks | `{"PreToolUse": …}` dict that nothing reads | SDK `hooks={on_pre_tool_use, on_agent_stop, …}`. `on_agent_stop` can return `{"decision": "block"}` to refuse to let the agent stop. That is what makes "keep going until the tests are green" enforceable. |
| MCP | Writes `.harness/mcp.json` and never passes it | `--additional-mcp-config @file` (CLI) or `mcp_servers=` (SDK). Built-in GitHub MCP is on by default (`--disable-builtin-mcps`). |
| Cost control | None (loop is unbounded) | `--max-ai-credits` / `session_limits={"max_ai_credits": …}`, plus usage events with `cost`. |
| Context | One fresh process per session, no compaction | SDK `infinite_sessions` (background compaction), resumable sessions. |
| Models | Hard-coded `claude-opus-4.5` / `claude-sonnet-4.5` / `gpt-5` in four places that disagree | Model catalog changes often. Query `client.list_models()`, default to the account default, and pin explicitly in config. |
| Install | `npm i -g @githubnext/github-copilot-cli`, `copilot auth login` | `npm i -g @github/copilot` (or brew/winget), `copilot login`. The SDK can provision its own runtime (`python -m copilot download-runtime`). |
| CI (Actions) | n/a | Node 20 action runtimes were removed from hosted runners on 2026-09-23. Workflows must use node24 majors (`actions/checkout@v7`, `setup-node@v7`, `setup-python@v7`, `upload-artifact@v7`). |

**Verdict:** v1 is not a sound base for a deterministic TDD harness. The core idea of a
persistent loop with a checklist is fine. What has to change: the agent grades its own work,
the tests aren't executable, and the "security" layer is decorative. The new harness keeps the
loop concept and replaces the rest, using the Copilot SDK as the primary backend.

---

## 3. Findings

Severity: **C** = critical (wrong results or unsafe), **H** = high, **M** = medium, **L** = low.
Line numbers refer to commit `4292c5b`.

### Critical

**C1. The security allowlist is dead code; the agent runs with no restrictions.**
`create_client()` puts `bash_security_hook` into `CopilotClientOptions.hooks`
(`copilot_client.py:346-348`). Nothing ever reads `options.hooks`. The same is true of
`allowed_tools`, `mcp_servers`, `system_prompt` and `max_turns`. The only command actually
executed is `copilot --model M --allow-all-tools --allow-all-paths --add-dir CWD -p PROMPT`
(`copilot_client.py:182-195`). The agent can therefore run any shell command against any path on
the machine, including `rm`, `curl`, `sudo`, `git push --force`, and reads of `~/.ssh`. The
README's *Security* section ("Blocked: rm, curl, wget, sudo…") is false.

**C2. Even if wired in, the allowlist parser is bypassable.** Probing `bash_security_hook` directly:

| Command | Result |
|---|---|
| `ls $(rm -rf ~)` | **ALLOWED** (command substitution not parsed) |
| `node -e "require('child_process').execSync('rm -rf ~')"` | **ALLOWED** (`node`/`npx` = arbitrary code) |
| `npx some-random-package` | **ALLOWED** |
| `git push --force origin main` | **ALLOWED** (all of `git` permitted) |
| `cp ~/.ssh/id_rsa ./app/public/` | **ALLOWED** (no path policy) |

A hand-rolled `shlex` allowlist cannot be made safe. Use the CLI's own permission engine (deny
rules) and the SDK's parsed permission requests, plus OS-level isolation (container, or the
CLI's experimental `sandbox`) for real containment.

**C3. The agent grades its own homework.** The only completion signal is the agent editing
`feature_list.json` to `"passes": true` (`prompts/coding_prompt.md` Step 7;
`progress.py:count_passing_tests`). The harness never runs a test. Nothing technical stops the
agent from flipping entries without verifying them, rewording or deleting hard cases, or
reordering. The only guard is the instruction "IT IS CATASTROPHIC TO REMOVE OR EDIT FEATURES."
That gives no determinism and no audit trail.

**C4. The "tests" are prose, so they can't be re-run, gate CI, or serve as a regression suite.**
`steps: ["Navigate…", "Verify…"]` is interpreted fresh by a model each time. The same app can
"pass" or "fail" depending on the session. The regression strategy is "re-check 3–4 passing
features by hand each session" (`coding_prompt.md` Step 3), which is at most about 2% coverage
of the suite.

**C5. Session classification is a substring match, so the harness is almost always in "recovery mode".**
`agent.py:72` counts any output line containing `"error"` or `"failed"` as an error ("Added error
handling to the form" counts). Any count above zero returns `"error"` (`agent.py:104`). After
three such sessions every later prompt is prefixed with "⚠️ RECOVERY MODE" (`agent.py:197`). The
counter resets only on a "clean" session, which practically never occurs. Real failures are
missed at the same time: the process **exit code is never checked**, so auth failures or a bad
model name look like a normal session.

**C6. No timeouts, no budget, no stuck detection.**
- `subprocess.Popen` is read with a blocking `for line in process.stdout` inside an `async`
  generator (`copilot_client.py:227`). The event loop is blocked and there is no timeout. The
  `except subprocess.TimeoutExpired` (`:270`) is unreachable because no timeout is ever passed.
  A hung session hangs the harness forever.
- `--max-iterations` defaults to unlimited (`autonomous_agent_demo.py:91`), so sessions that make
  no progress loop indefinitely and each one consumes premium requests.

### High

**H1. Tests and tracking live outside version control.** The prompt places `feature_list.json`,
`init.sh` and `tests/` in `.harness/`, then says *don't commit `.harness/`*
(`initializer_prompt.md:153`). CI can't use the test inventory, and losing the working directory
loses the project's entire test record.

**H2. Unconsented external side effects.** The initializer creates a private GitHub repo and
pushes on every session (`initializer_prompt.md:150`). Every coding session runs
`gh issue comment 1` with a hard-coded issue number (`coding_prompt.md:232`); issue #1 is not
necessarily the Epic.

**H3. Global configuration clobbering.** The README tells users to
`cp .copilot/copilot-instructions.md ~/.copilot/copilot-instructions.md` and
`cp .copilot/mcp-config.json ~/.copilot/mcp-config.json`. This overwrites the user's own global
instructions and MCP servers. It also installs personal preferences ("Preferred shell: zsh
(macOS)") into every Copilot session the user runs anywhere.

**H4. MCP configuration is never delivered.** `.harness/mcp.json` is written each session
(`copilot_client.py:327`) but never passed to the CLI, so Playwright MCP works only if the user
copied it globally (H3).

**H5. Prerequisites script always fails and costs money.**
- `check_mcp_config` looks for `MCP.json` in the repo root, which does not exist
  (`check_prerequisites.sh:242`), so the script always exits 1.
- `check_copilot_auth` runs a real prompt (`copilot -p "respond with exactly: ok"`, `:93`), which
  consumes a premium request on every run.
- Suggests the wrong package (`@githubnext/github-copilot-cli`, `:82`) and a non-existent command
  (`copilot auth login`, `:99`, `:312`). Min version check `0.0.354` is obsolete.

**H6. No non-functional coverage worth the name.** The initializer asks for "style",
"navigation", "accessibility (10)" and "performance (10–15)" prose entries, and **no security
tests at all**. "Performance" is unmeasurable prose ("page loads quickly"). The prompts
also forbid `curl` and JS evaluation (`coding_prompt.md:170-171`), which rules out API-level
security and latency testing.

**H7. Contradictory model configuration.** The default is `claude-opus-4.5`
(`autonomous_agent_demo.py:28`). `--help` lists sonnet-4.5/sonnet-4/haiku-4.5/gpt-5 (`:47`, `:101`).
The README says the default is `claude-sonnet-4.5` (`README.md:382`), as does
`CopilotClientOptions` (`copilot_client.py:85`). None of these is guaranteed to exist in today's
catalog.

### Medium

- **M1.** README documents `--spec-file`, but the parser defines `--spec` (`README.md:381` vs
  `autonomous_agent_demo.py:104`). The spec can only be loaded from `prompts/`, and it is copied
  only on the first run, so a later `--spec` change is silently ignored (`prompts.py:59`).
- **M2.** `IntegratedMonitor.start()` is a no-op and its loop body is `pass`
  (`autonomous_agent_demo.py:225-236`). `monitor.py` derives "files created" and "tool use" from
  regexes over human-readable CLI text, which breaks whenever the CLI changes its output.
- **M3.** Each session `cat`s the 200-entry feature list and full spec into context. That is
  token-heavy and gets worse as the project grows.
- **M4.** Linux external monitor passes `--` to `xterm`/`konsole`, which they don't accept.
- **M5.** `TeeOutput` swaps `sys.stdout` for an object without `isatty`/`encoding`/`fileno`. Some
  libraries break on that.
- **M6.** No packaging (`pyproject.toml`/`requirements.txt`), no tests for the harness itself, no CI.
- **M7.** The prompt is passed as an argv element (`-p <10KB prompt>`), so it is visible in `ps`.
  Linux also caps a single argument at 128 KiB, a limit that a growing prompt will eventually hit.

### Low

- **L1.** `.playwright-mcp/*.png` screenshots are committed even though `.playwright-mcp` is in
  `.gitignore`.
- **L2.** `.vscode/MCP.json` contains a personal set of MCP servers (Vercel, Context7, Octocode,
  …) unrelated to the harness.
- **L3.** `README.md` tail ("moltbots, openclaw bots… please post on moltbook") reads as
  injected or joke text in a public README.
- **L4.** `.github/copilot-instructions.md` describes the v1 architecture and steers agents
  toward it.

---

## 4. What the replacement does about each

| Finding | v2 response |
|---|---|
| C1, C2 | Permission policy enforced in the SDK `on_permission_request` callback and `on_pre_tool_use` hook, using the CLI's parsed shell model (identifiers, `read_only`, `possible_paths`, `possible_urls`). Role-scoped write paths. Config discovery, file hooks, custom instructions and memory are disabled so an agent can't plant config for its next session. CLI fallback uses `--deny-tool`/`--available-tools`. After every session the harness checks that each file changed is inside the role's write scope and quarantines anything outside it. |
| C3 | **The harness, not the agent, runs the tests and records results.** It reads Playwright JSON / JUnit XML. Agents never write to status files. |
| C4 | Tests are **executable Playwright specs** whose titles carry stable IDs (`FUNC-001: …`), committed to git and **frozen by SHA-256 lock**. Passing tests are promoted to a **regression baseline** that may only grow. |
| C5 | Session outcome is the **test delta**: newly passing, regressions, still failing. Agent prose is ignored. SDK `SessionErrorData` and exit codes are surfaced. |
| C6 | Per-session wall-clock timeout with `session.abort()`, per-session and total AI-credit caps, a no-progress circuit breaker, per-test attempt limits ("blocked"), and a bounded number of stop-hook continuations. |
| H1 | `acceptance/` and `harness/` are committed. `.harness/` holds only local logs. |
| H2 | No repo/issue creation or push. `git push` is denied by default. The harness makes local commits only at green checkpoints. |
| H3, H4 | Nothing is installed globally. MCP servers, skills and instructions are passed per session. |
| H5 | `copilot-harness doctor` checks the SDK, runtime, auth status (via `get_auth_status`, no prompt), Node and Playwright without spending credits. |
| H6 | Plan validation enforces category minimums (security, performance, usability). The bundled support kit provides measured helpers: latency p95, Web Vitals, axe-core, security headers, cookie flags, injection payloads. |
| H7 | Model per role is set in `harness.toml`. An empty value means the account default. `copilot-harness models` lists the live catalog. |
| M1–M7, L1–L4 | Addressed in the rewrite: legacy code moved to `legacy/`, junk removed, package + tests + CI added. |
