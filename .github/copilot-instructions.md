# copilot-harness — instructions for AI agents working on this repository

This repo is **copilot-harness v2**, a test-driven development harness that drives GitHub
Copilot through the Python Copilot SDK (`github-copilot-sdk`). Read `docs/ARCHITECTURE.md`
first. `legacy/` holds the deprecated v1 harness: do not extend it.

## Layout
- `copilot_harness/orchestrator.py`: pipeline phases, sessions, scope enforcement, ratchet, disputes.
- `copilot_harness/policy.py`: pure permission logic. Every rule needs a test in `tests/test_policy.py`.
- `copilot_harness/backends/`: `sdk.py` (default), `cli.py` (fallback), `fake.py` (tests).
- `copilot_harness/prompts/`: one prompt per agent role. `_common.md` is appended to every
  session as system instructions. Templates use `$name` placeholders (`string.Template`).
- `copilot_harness/templates/`: copied into generated projects (acceptance kit, CI, gitignore).
  `acceptance/scripts/gate.mjs` must stay dependency-free and mirror `lock.py`'s locked-file rules.

## Invariants (do not break)
1. Agents never decide pass/fail. Only `runner.py` results feed the baseline.
2. The baseline only grows, except through explicit retirement (`--allow-retire`).
3. Frozen files are covered by `harness/tests.lock.json`. Any path added to the freeze must be
   added to `lock.py` **and** `gate.mjs`.
4. Each role writes only its scope (`ROLE_WRITE_SCOPES`). The coder never writes `acceptance/` or `harness/`.
5. Phases are derived from committed artifacts (`Harness.current_phase`), so `run` stays resumable.

## Checks before committing
```bash
ruff check .
pytest                       # fast, no network
HARNESS_E2E=1 pytest -m e2e  # when touching runner/templates/gate (needs Node + Chromium)
```
