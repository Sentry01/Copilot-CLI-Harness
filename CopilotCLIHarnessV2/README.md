# Copilot CLI Harness V2

> Stop babysitting your AI coding assistant — and track its work where your team already lives: **GitHub Issues**.

CopilotHarness wraps GitHub Copilot CLI in a persistent **Plan → Code → Verify → Repeat**
loop. You give it a spec; it stands up a repo, breaks the work into a GitHub Issues
backlog, then grinds through the issues one verified feature at a time until they are
all closed.

V2 is a ground-up rewrite of the original harness around three changes:

| | V1 | **V2** |
|---|---|---|
| Feature backlog | `feature_list.json` (hand-parsed, corrupts at ~200 items) | **GitHub Issues + sub-issues** — closed issue = verified feature |
| Copilot integration | `subprocess` + regex-scraping stdout | **[Copilot SDK](https://www.npmjs.com/package/@github/copilot-sdk)** (GA, June 2026) with real streamed events |
| Command security | allowlist that was **never wired up** (`--allow-all-tools`) | allowlist enforced through a real **SDK permission handler** |
| Progress source of truth | JSON *and* a mirrored Epic issue (they drift) | one source: the issues themselves |

## Why Issues instead of JSON

- **One source of truth.** V1 kept progress in JSON *and* told the agent to mirror it
  onto an Epic — the two inevitably drifted. Here the issues *are* the backlog.
- **No fragile JSON.** V1's initializer wrote the 200-item list in a 7-stage append
  dance specifically because writing it all at once kept corrupting the file. Creating
  issues sidesteps that entirely.
- **Free tooling.** Timeline, assignees, a Projects board view, and human edits
  mid-run — all without any extra harness code.
- **Auditability.** Every "verified" is a closed issue with screenshots in the comment.

Each feature is an issue labelled `feature` + a category (`core`, `navigation`,
`style`, `edge`, `accessibility`, `performance`, `integration`), created as a
sub-issue of one Epic. **Closing the issue marks the feature verified.** Progress is
`closed feature issues / all feature issues`.

## How it works

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

The Python harness only ever **reads** progress (via the GitHub REST API — no `gh`
dependency on the orchestrator side). The agent does all the issue creation/closing
from inside its Copilot sessions.

## Install

```bash
# 1. Copilot CLI (see https://docs.github.com/copilot for your platform)
npm install -g @github/copilot

# 2. The harness + the Copilot SDK
pip install -e .
python -m copilot download-runtime   # one-time SDK runtime fetch

# 3. Auth: gh for the agent's git/issue ops; a token for the harness to read progress
gh auth login
export GH_TOKEN=$(gh auth token)     # or COPILOT_GITHUB_TOKEN / GITHUB_TOKEN

# 4. Browser verification
npx playwright install chromium
```

## Run

```bash
# Bare names are created under $HOME/Projects/
copilot-harness --project-dir my_app

# Pick a model, cap sessions for a dry run
copilot-harness --project-dir my_app --model gpt-5 --max-iterations 3
```

Define what to build by editing `copilot_harness/prompts/app_spec.txt` (XML), or pass
your own with `--spec-file my_spec.txt`.

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--project-dir` | Project name or path. Bare names go under `$HOME/Projects/`. | `autonomous_demo_project` |
| `--spec-file` | Spec file in `copilot_harness/prompts/`. | `app_spec.txt` |
| `--model` | Copilot model. Known: `claude-sonnet-4.5`, `claude-haiku-4.5`, `gpt-5`. | `claude-sonnet-4.5` |
| `--max-iterations` | Stop after N sessions. | unlimited |
| `--permission-mode` | `allowlist` enforces the command allowlist; `approve-all` disables it. | `allowlist` |
| `--github-token` | Token to read progress (else `COPILOT_GITHUB_TOKEN`/`GH_TOKEN`/`GITHUB_TOKEN`). | env |
| `--quiet` | Less console output. | off |

## Security

Unlike V1, the allowlist is **actually enforced**. Every shell command the agent
proposes runs through `security.evaluate_command`, wired into the SDK's
`on_permission_request` handler. Allowed: `git`, `gh`, `npm`/`npx`/`node`, `ls`,
`cat`, `grep`, … Blocked: `rm`, `curl`, `wget`, `sudo`, and anything not on the list.
Use `--permission-mode approve-all` only when you trust the environment.

## Project layout

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
```

Each project the harness builds gets:

```
my_app/
├── app/          # the deployable application (its own git repo + GitHub repo)
└── .harness/     # app_spec.txt, repo.json (owner/repo/epic), init.sh, logs/
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Documentation

- [`REPORT.md`](REPORT.md) — project report: the V1 review that motivated the
  rewrite, what V2 is, test status, and how to stand up the standalone repo.
- [`docs/MIGRATION.md`](docs/MIGRATION.md) — detailed, item-by-item V1 → V2 changelog.

## License

MIT. See `LICENSE`.
