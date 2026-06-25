"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio

from .agent import run_autonomous_agent
from .config import DEFAULT_MODEL, KNOWN_MODELS, resolve_project_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copilot-harness",
        description="Autonomous coding agent for GitHub Copilot CLI (Issues-backed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  copilot-harness --project-dir my_app\n"
            "  copilot-harness --project-dir my_app --model gpt-5 --max-iterations 5\n"
            "  copilot-harness --project-dir my_app --permission-mode approve-all\n"
        ),
    )
    parser.add_argument(
        "--project-dir",
        default="autonomous_demo_project",
        help="Project name or path. Bare names are created under $HOME/Projects/.",
    )
    parser.add_argument(
        "--spec-file",
        default="app_spec.txt",
        help="Spec file in the prompts/ directory (default: app_spec.txt).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Copilot model (default: {DEFAULT_MODEL}). Known: {', '.join(KNOWN_MODELS)}.",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Stop after N sessions (default: unlimited).",
    )
    parser.add_argument(
        "--permission-mode", choices=("allowlist", "approve-all"), default="allowlist",
        help="allowlist enforces the command allowlist (default); approve-all disables it.",
    )
    parser.add_argument(
        "--github-token", default=None,
        help="Token for reading progress. Falls back to "
             "COPILOT_GITHUB_TOKEN/GH_TOKEN/GITHUB_TOKEN.",
    )
    parser.add_argument("--quiet", action="store_true", help="Reduce console output.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    project_dir = resolve_project_dir(args.project_dir)
    try:
        asyncio.run(
            run_autonomous_agent(
                project_dir=project_dir,
                model=args.model,
                max_iterations=args.max_iterations,
                spec_file=args.spec_file,
                permission_mode=args.permission_mode,
                github_token=args.github_token,
                verbose=not args.quiet,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.")


if __name__ == "__main__":
    main()
