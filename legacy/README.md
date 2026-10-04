# Legacy harness (v1) — deprecated

This is the original CopilotHarness (checklist-driven, `copilot -p` subprocess loop), kept
unchanged for reference and comparison. It is **not maintained**. See
[`docs/REVIEW.md`](../docs/REVIEW.md) for why it was replaced. In short:

- the Bash allowlist in `security.py` is never invoked, so sessions run with `--allow-all-tools --allow-all-paths`;
- the agent marks its own tests as passing in `feature_list.json`;
- tests are prose, not executable, so there is no regression suite and no CI.

If you still want to run it (from this directory):

```bash
python autonomous_agent_demo.py --project-dir my_app --spec app_spec.txt
```

Use the v2 harness instead: `copilot-harness new my_app --prd PRD.md && copilot-harness run my_app`.
`prompts/app_spec.txt` remains a useful example of a detailed product spec. You can pass it to v2 as `--prd`.
