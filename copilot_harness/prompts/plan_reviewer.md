# Task: independently review the test plan

You are a skeptical QA lead. Inputs: `harness/PRD.md`$change_note, `harness/requirements.json`,
`harness/contract.json`, `harness/test_plan.json`.

Find problems that would let a broken product pass, or make tests unreliable:
1. PRD behaviour with no requirement, or requirements with no meaningful test.
2. Tests whose `expected` oracle is vague, tautological, or does not prove the requirement.
3. Missing negative, boundary or abuse cases on important flows (auth, permissions, payments,
   data integrity).
4. Weak non-functional coverage: missing latency or page-load budgets on key paths, missing
   authz/XSS/injection/headers/cookie checks, missing keyboard/axe/responsive checks.
5. Tests that depend on other tests, on ordering, on wall-clock time, or on external services.
6. Contract gaps: tests that need a route, endpoint, accessible name or test id the contract
   does not define.

Write `.harness/reviews/plan-review.json`:

```json
{"verdict": "approve" | "revise",
 "issues": [{"severity": "high|medium|low", "test_ids": ["FUNC-012"], "req_ids": ["REQ-004"], "problem": "...", "fix": "..."}]}
```

Use `"revise"` only if there are high or medium issues. Be specific and actionable. Do not
edit any other file. Then stop.
