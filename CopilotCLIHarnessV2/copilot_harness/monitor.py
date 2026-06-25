"""
Progress monitor
================

Trimmed down from V1. Because the SDK gives us structured results, the monitor no
longer needs to regex-scrape stdout to guess tool use and errors. It just logs
sessions and renders progress. (V1's ``IntegratedMonitor`` was dead code — its
``start()`` did nothing and its tail loop ``pass``-ed every line.)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .github_backend import ProgressSummary


class ProgressMonitor:
    def __init__(self, project_dir: Path, verbose: bool = True):
        self.project_dir = Path(project_dir)
        self.verbose = verbose
        self.log_dir = self.project_dir / ".harness" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"run_{stamp}.log"
        self.json_log_file = self.log_dir / f"run_{stamp}.json"
        self.sessions: list[dict] = []
        self._write(f"=== Run started {stamp} :: {self.project_dir} ===\n")

    def _write(self, text: str) -> None:
        with open(self.log_file, "a") as fh:
            fh.write(text)

    def _emit(self, text: str) -> None:
        self._write(text + "\n")
        if self.verbose:
            print(text)

    def stream(self, chunk: str) -> None:
        """Sink for live model output."""
        self._write(chunk)
        if self.verbose:
            print(chunk, end="", flush=True)

    def session_start(self, num: int, kind: str) -> None:
        self._current = {"num": num, "kind": kind, "start": datetime.now().isoformat()}
        self._emit(f"\n{'=' * 70}\n  SESSION {num}: {kind.upper()}\n{'=' * 70}")

    def session_end(self, status: str, tool_calls: int, errors: list[str], blocked: list[str]) -> None:
        cur = getattr(self, "_current", {"num": "?", "kind": "?", "start": datetime.now().isoformat()})
        start = datetime.fromisoformat(cur["start"])
        cur.update(
            end=datetime.now().isoformat(),
            duration=(datetime.now() - start).total_seconds(),
            status=status,
            tool_calls=tool_calls,
            errors=errors,
            blocked=blocked,
        )
        self.sessions.append(cur)
        self._emit(
            f"\n--- Session {cur['num']} :: {status} "
            f"({cur['duration']:.0f}s, {tool_calls} tool calls, "
            f"{len(errors)} errors, {len(blocked)} blocked) ---"
        )
        if blocked:
            for item in blocked[:5]:
                self._emit(f"   [BLOCKED] {item}")
        self._save_json()

    def progress(self, summary: ProgressSummary) -> None:
        if summary.total == 0:
            self._emit("\n📊 Progress: no feature issues yet")
            return
        width = 30
        filled = int(width * summary.verified / summary.total)
        bar = "█" * filled + "░" * (width - filled)
        self._emit(
            f"\n📊 Progress: [{bar}] {summary.verified}/{summary.total} ({summary.percent:.1f}%)"
        )
        for cat, stats in sorted(summary.by_category.items()):
            mark = "✅" if stats["verified"] == stats["total"] else "🔄"
            self._emit(f"   {mark} {cat}: {stats['verified']}/{stats['total']}")

    def _save_json(self) -> None:
        with open(self.json_log_file, "w") as fh:
            json.dump({"project": str(self.project_dir), "sessions": self.sessions}, fh, indent=2)

    def final_summary(self) -> None:
        total_errors = sum(len(s.get("errors", [])) for s in self.sessions)
        self._emit(
            f"\n{'=' * 70}\n  RUN COMPLETE\n{'=' * 70}\n"
            f"  Sessions: {len(self.sessions)}  Errors: {total_errors}\n"
            f"  Logs: {self.log_file}"
        )
        self._save_json()


def get_monitor(project_dir: Path, verbose: bool = True) -> ProgressMonitor:
    return ProgressMonitor(project_dir, verbose)
