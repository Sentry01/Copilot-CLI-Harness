# Task: adjudicate a dispute about an acceptance test

The coding agent claims test **$test_id** is defective:

```json
$claim
```

The test as planned:
```json
$case
```

Its code is in `$spec_file`. The latest failure:
```
$failure
```

Read `harness/PRD.md`, `harness/requirements.json` (requirements $req_ids) and
`harness/contract.json`. Decide independently. Your default is **uphold**: the test stands
unless it truly contradicts the PRD, the requirements or the contract, cannot be satisfied by
any correct implementation, or is nondeterministic. "It is hard to implement" is not a defect.

Write `.harness/disputes/decisions/$test_id.json`:

```json
{"test_id": "$test_id", "decision": "uphold" | "amend",
 "rationale": "short, specific reasoning citing the PRD/contract",
 "required_change": "if amend: exactly what the test must assert instead; otherwise empty"}
```

Do not modify any other file. Then stop.
