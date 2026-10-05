# Task: $mode_title

$mode_intro

## Target tests
```json
$cases
```

## Current failures (from the harness's own run)
```
$failures
```
$adjudications
## How to work
1. Read the failing specs ($spec_files), `harness/contract.json` and the relevant code in `app/`.
2. Implement the behaviour in `app/`, following the contract exactly: routes, endpoints, status
   codes, accessible names, test ids and messages.
3. Run the target tests yourself:
   `cd acceptance && npx playwright test --grep "$grep"`
   The runner builds nothing. Run the app's build command from `harness/app-contract.json` first
   if your stack needs a build. Playwright starts the app itself, so do not leave a server
   running on the app's port.
4. Before stopping, run the **whole** suite once (`cd acceptance && npx playwright test`) and
   make sure every test that passed before still passes. The harness rejects any session that
   breaks a passing test.

## Rules
- Tests are the specification and they are frozen. You cannot edit `acceptance/` or
  `harness/` (except `harness/app-contract.json`, e.g. to change install, build or start
  commands).
- Never special-case the tests: no detection of test data, user agents, `NODE_ENV=test` or
  the test hooks outside the reset hook itself. Implement the real behaviour for real users.
- Production quality: validate input on the server, use parameterised queries, encode output,
  check authorisation on every resource, hash passwords (bcrypt/argon2/scrypt), set secure
  cookie flags, return JSON errors without stack traces, and handle empty, loading and error
  states in the UI.
- Serve every asset (scripts, styles, fonts, images) from the app. The test runner blocks all
  other hosts, so CDN or third-party resources won't load in tests.
- If, and only if, a test contradicts the PRD, the contract, or itself, file a dispute instead
  of working around it. Write `.harness/disputes/claims/<TEST-ID>.json` as
  `{"test_id": "...", "claim": "why the test is wrong", "evidence": "PRD/contract quotes, failure output"}`.
  An independent adjudicator decides. Disputes are limited, so use them only for genuine test
  defects.
- Stop when the target tests pass and nothing else broke. If a target is still failing at the
  end, say precisely what remains in your final message.
