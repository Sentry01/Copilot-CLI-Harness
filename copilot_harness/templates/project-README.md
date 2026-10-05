# $name

Built test-first by [copilot-harness](https://github.com/Sentry01/Copilot-CLI-Harness).

| Path | What it is | Who may change it |
|---|---|---|
| `harness/PRD.md`, `harness/changes/` | Product requirements and change requests | You |
| `harness/requirements.json` | Atomic, testable requirements traced from the PRD | Harness (append-only) |
| `harness/contract.json` | API/UI contract the tests and the app share | Harness |
| `harness/test_plan.json` | Every acceptance test, traced to requirements | Harness (append-only) |
| `acceptance/` | Executable acceptance suite (Playwright) | Frozen; see `harness/tests.lock.json` |
| `harness/baseline.json` | Tests that passed stably; CI fails if any regresses | Harness (only grows) |
| `app/` | The implementation | Coding agent / you |

```bash
copilot-harness status .          # progress, baseline size, blocked tests
copilot-harness verify .          # run the suite and check for regressions
copilot-harness feature . --prd-delta change.md   # add a feature test-first
copilot-harness report .          # write harness/REPORT.md (traceability matrix)
```

CI (`.github/workflows/acceptance.yml`) runs the whole suite on every PR and fails on any
baseline regression or on edits to frozen tests. Protect `acceptance/`, `harness/` and
`.github/workflows/` with CODEOWNERS and make the `acceptance` check required.
