# Copilot CLI Harness - AI Agent Instructions

## Project Overview

This harness transforms GitHub Copilot CLI into an autonomous software engineer. It wraps the CLI in a persistent loop that plans, implements, verifies, and iterates on apps based on plain-text specifications.

**Core Pattern**: Two-agent architecture:
1. **Initializer Agent** (Session 1): Creates `feature_list.json` (200+ test cases) and project scaffolding
2. **Coding Agent** (Sessions 2+): Implements features one-by-one, verifying each before proceeding

## Architecture

```
autonomous_agent_demo.py  → Entry point, handles CLI args and logging
    └── agent.py          → Main loop: pick task → code → verify → repeat
        └── copilot_client.py  → Spawns `copilot` CLI subprocess, streams output
            └── security.py    → Allowlist filter for bash commands
```

**Key flows:**
- `run_autonomous_agent()` in `agent.py` is the main loop
- `CopilotCLIClient` wraps subprocess calls to `copilot --model X --allow-all-tools`
- Progress tracked via `feature_list.json` (tests) and `progress.json` (session notes)

## Directory Structure Convention

Generated projects use a **two-folder separation**:
```
project_root/
├── app/          ← Deployable application code ONLY
└── .harness/     ← Operational files (specs, tests, logs, tracking)
```

**Critical**: The agent's working directory (`cwd`) is set to `app/` — all file operations are relative to that. References to `.harness/` must use `../.harness/`.

## Key Files to Understand

| File | Purpose |
|------|---------|
| `copilot_client.py` | CLI subprocess management, Playwright MCP setup |
| `security.py` | Command allowlist (`ALLOWED_COMMANDS` dict) |
| `prompts/initializer_prompt.md` | Template for Session 1 |
| `prompts/coding_prompt.md` | Template for Sessions 2+ |
| `progress.py` | `count_passing_tests()` for completion detection |
| `monitor.py` | Real-time log parsing and session tracking |

## Security Model

The harness uses an **allowlist approach** in `security.py`:
- Only commands in `ALLOWED_COMMANDS` set are permitted
- Special validation for `pkill` (dev processes only), `chmod` (+x only), `init.sh`
- Never add `rm`, `curl`, `wget`, `sudo` to the allowlist

To add a new allowed command:
```python
# In security.py, add to ALLOWED_COMMANDS set
ALLOWED_COMMANDS = {
    "ls", "cat", "npm", "node", ...
    "your_command",  # Add here with comment explaining why
}
```

## Prompt Modification

Prompts live in `prompts/` directory and are loaded by `prompts.py`:
- `initializer_prompt.md` → First session (creates test list)
- `coding_prompt.md` → All subsequent sessions

**Key sections in prompts:**
1. `STATUS:` updates — Required for monitoring visibility
2. Step-by-step workflow — Mandatory sequence the agent follows
3. File placement rules — Keeps `app/` deployable

## Testing Philosophy

This project uses **checklist-driven testing**, not traditional unit tests:
- `feature_list.json` contains 200+ test case objects with `"passes": false/true`
- Verification happens via **Playwright MCP browser automation**
- Project is "done" when `count_passing_tests() == total_tests`

## Common Development Tasks

### Run the harness locally
```bash
python autonomous_agent_demo.py --project-dir my_app --external-monitor
```

### Monitor a running session
```bash
./monitor_agent.sh --project my_app
# Or tail the log directly:
tail -f harness_logs/my_app_*.log
```

### Test security hook changes
```python
# In security.py, the bash_security_hook is async:
await bash_security_hook({"tool_name": "Bash", "tool_input": {"command": "..."}})
# Returns {} to allow, {"decision": "block", "reason": "..."} to block
```

## Error Handling Patterns

The harness has built-in recovery:
- `MAX_ERROR_RETRIES = 3` consecutive errors triggers recovery mode
- Recovery mode adds diagnostic instructions to the prompt
- See `run_autonomous_agent()` in `agent.py` (lines 70-120)

## Integration Points

- **GitHub CLI** (`gh`): Used for repo creation and issue tracking
- **Playwright MCP**: Browser automation via `npx @playwright/mcp@latest`
- **Git**: Auto-commits after each verified feature

## When Modifying This Codebase

1. **Adding new tools**: Update `ALLOWED_COMMANDS` in security.py
2. **Changing prompts**: Edit `prompts/*.md`, not embedded strings
3. **Adding monitoring**: Extend `ProgressMonitor` in monitor.py
4. **New CLI flags**: Add to `parse_args()` in autonomous_agent_demo.py
