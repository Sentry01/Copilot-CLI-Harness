# CopilotCLIHarnessV2 — Project Report

**Date:** 2026-06-25
**Status:** Code complete, tests passing. Delivered via draft PR (see *Delivery notes*).

This report covers (1) the review of the original `Copilot-CLI-Harness` that
motivated the rewrite, (2) what V2 is and how it works, and (3) how to stand up the
standalone repository. The item-by-item engineering changelog lives in
[`docs/MIGRATION.md`](docs/MIGRATION.md).

---

## 1. Review of V1

A review of the original harness surfaced one serious bug and a cluster of
drift / dead-code issues.

### 🔴 The security allowlist was never enforced (most serious)

`security.py` defined `bash_security_hook`, and `create_client()` attached it to a
`CopilotClientOptions.hooks` field — but `copilot_client.receive_response()` built
its own subprocess command:

```
copilot -p <prompt> --allow-all-tools --allow-all-paths ...
```

and **never passed the hook to it**. The hook was dead code. Meanwhile the README
advertised that `rm`, `curl`, `sudo`, etc. were blocked. In reality the agent ran
completely unrestricted.

### Other findings

| # | Finding | Impact |
|---|---------|--------|
| 2 | **Stdout-scraping instead of an API.** The client reconstructed "messages" by string-matching stdout (`✓`, `Create`, `[Tool: X]`). Error detection was `if "error" in text.lower()`. | A model saying "no errors found" registered as an error; tool/finish detection was brittle. |
| 3 | **Two drifting trackers.** Progress lived in `feature_list.json` *and* a mirrored Epic issue. | The two inevitably diverged. The initializer wrote the 200-item JSON in a 7-stage append dance purely to avoid corrupting the file. |
| 4 | **Config drift.** Three different default models across the demo, client, and README. CLI flag was `--spec` in code but `--spec-file` in docs. | The documented command failed; unclear which model actually ran. |
| 5 | **Dead code.** `IntegratedMonitor.start()` did nothing; its tail loop `pass`-ed every line. | Misleading; maintenance burden. |
| 6 | **Packaging gaps.** No `pyproject.toml`/pinned deps; README referenced a non-existent `assets/demo.gif`. | Hard to install reproducibly; broken docs. |

---

## 2. What V2 is

A ground-up rewrite as a proper `copilot_harness` Python package that keeps the
**Plan → Code → Verify → Repeat** loop but fixes the above. Three headline changes:

| | V1 | **V2** |
|---|---|---|
| Feature backlog | `feature_list.json` (corrupts at ~200 items) | **GitHub Issues + sub-issues** — closed issue = verified feature |
| Copilot integration | `subprocess` + regex-scraping stdout | **Copilot SDK** (`github-copilot-sdk`, GA Jun 2026) with real streamed events |
| Command security | allowlist that was never wired up | allowlist enforced through a real SDK **permission handler**, with unit tests |

### The GitHub Issues backend (the headline request)

`github_backend.py` replaces `feature_list.json`:

- Each feature is an issue labelled `feature` + one category label (`core`,
  `navigation`, `style`, `edge`, `accessibility`, `performance`, `integration`),
  created as a **sub-issue of one Epic**.
- **Closing the issue marks the feature verified.**
- Progress = `closed feature issues / all feature issues`, computed by
  `summarize_issues()`.

Why it is better: one source of truth (no JSON/Epic drift), no fragile bulk-JSON
write, and the Projects board / timeline / human edits come for free. The harness
only ever *reads* progress — over the GitHub REST API using only the standard
library, so the orchestrator does not depend on the `gh` CLI. The agent does all
issue creation/closing from inside its Copilot sessions.

### How the loop runs

```
spec ──▶ Initializer session ──▶ creates repo, Epic, one issue per feature
                                          │
        ┌─────────────────────────────────┘
        ▼
   Coding session: pick an open feature issue ─▶ implement
        ─▶ verify in a real browser (Playwright MCP)
        ─▶ close the issue (= verified) ─▶ commit & push ─▶ update Epic
        ▲                                                        │
        └──────────────── repeat until every issue is closed ◀──┘
```

### Security, now enforced

Every shell command the agent proposes runs through `security.evaluate_command`,
wired into the SDK's `on_permission_request` handler
(`sdk_client.make_permission_handler`). Allowed: `git`, `gh`, `npm`/`npx`/`node`,
`ls`, `cat`, `grep`, … Blocked: `rm`, `curl`, `wget`, `sudo`, and anything not on the
list. `--permission-mode approve-all` is an explicit opt-out instead of the silent
default. The logic is pure and unit-tested (V1 had no tests).

### Package layout

```
copilot_harness/
├── agent.py           # the Plan→Code→Verify loop
├── sdk_client.py      # Copilot SDK wrapper + permission handler
├── github_backend.py  # GitHub Issues backlog (replaces feature_list.json)
├── security.py        # command allowlist (pure, unit-tested)
├── monitor.py         # logging + progress rendering
├── config.py          # single source of defaults
├── cli.py             # argument parsing / entry point
└── prompts/           # initializer + coding prompts, app_spec.txt
tests/                 # unit tests for security + github_backend
docs/MIGRATION.md      # detailed V1→V2 changelog
```

### Test status

`pytest` — **33 passing**, covering the command allowlist and the Issues
aggregation/state logic. The SDK-dependent path is import-isolated so the package and
its tests run without the SDK or its runtime installed.

```bash
pip install -e ".[dev]"
pytest          # 33 passed
```

---

## 3. Delivery notes & standing up the standalone repo

The intent was a standalone private repo named **`CopilotCLIHarnessV2`**. Creating
it from the build session was not possible: the GitHub integration backing the
session cannot create repositories (`403 Resource not accessible by integration`),
and git push was pinned to the existing `Copilot-CLI-Harness` remote. The complete
project was therefore delivered as the `CopilotCLIHarnessV2/` subtree on a draft PR
against `Copilot-CLI-Harness`.

To lift it into its own repository:

```bash
cd Copilot-CLI-Harness/CopilotCLIHarnessV2
git init -b main && git add . && git commit -m "Initial commit"
gh repo create CopilotCLIHarnessV2 --private --source=. --push
```

Once the repo exists, drop the `CopilotCLIHarnessV2/` nesting (the subtree *is* the
repo root) and update `[project.urls]` in `pyproject.toml` if needed.

---

## 4. Ideas intentionally deferred

- **Projects v2 board** with a real Status field (issues already carry state via
  open/closed + category labels).
- **Cost/usage tracking** — the SDK exposes per-session token usage; surface it in the
  monitor and Epic comments.
- **Parallel feature agents** building independent `core` features in separate
  worktrees, each closing its own issue.
- **Resumable sessions** — the SDK supports session persistence; the loop currently
  starts each session fresh by design.

## References

- Copilot SDK GA announcement — https://github.blog/changelog/2026-06-02-copilot-sdk-is-now-generally-available/
- `github/copilot-sdk` — https://github.com/github/copilot-sdk
- Python SDK README — https://raw.githubusercontent.com/github/copilot-sdk/main/python/README.md
