"""Thin git wrapper. Only the harness commits; agents get read-only git."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

HARNESS_AUTHOR = ("copilot-harness", "copilot-harness@users.noreply.github.com")


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class StatusEntry:
    code: str  # porcelain XY, e.g. " M", "??", "D "
    path: str

    @property
    def untracked(self) -> bool:
        return self.code == "??"


class Git:
    def __init__(self, root: Path):
        self.root = Path(root)

    def run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout

    def is_repo(self) -> bool:
        return (self.root / ".git").exists()

    def init(self) -> None:
        if not self.is_repo():
            self.run("init", "-q", "-b", "main")
        if not self.run("config", "--get", "user.email", check=False).strip():
            self.run("config", "user.name", HARNESS_AUTHOR[0])
            self.run("config", "user.email", HARNESS_AUTHOR[1])

    def head(self) -> str:
        return self.run("rev-parse", "HEAD", check=False).strip()

    def status(self) -> list[StatusEntry]:
        out = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = []
        parts = out.split("\0")
        i = 0
        while i < len(parts):
            item = parts[i]
            if not item:
                i += 1
                continue
            code, path = item[:2], item[3:]
            entries.append(StatusEntry(code, path))
            if code[0] in "RC":  # rename/copy: next field is the source path
                i += 1
            i += 1
        return entries

    def commit_all(self, message: str) -> str:
        """Stage everything (respecting .gitignore) and commit; returns the new HEAD."""
        self.run("add", "-A")
        if not self.run("status", "--porcelain").strip():
            return self.head()
        self.run("commit", "-q", "--no-verify", "-m", message)
        return self.head()

    def commit_paths(self, message: str, paths: list[str]) -> str:
        """Commit only ``paths``; anything else already staged stays staged and uncommitted."""
        existing = [p for p in paths if (self.root / p).exists() or self.tracked_at("HEAD", p)]
        if not existing:
            return self.head()
        self.run("add", "-A", "--", *existing)
        if not self.run("diff", "--cached", "--name-only", "--", *existing).strip():
            return self.head()
        self.run("commit", "-q", "--no-verify", "--only", "-m", message, "--", *existing)
        return self.head()

    def checkout_paths(self, ref: str, paths: list[str]) -> None:
        if paths:
            self.run("checkout", ref, "--", *paths)

    def tracked_at(self, ref: str, path: str) -> bool:
        return bool(self.run("ls-tree", "--name-only", ref, "--", path, check=False).strip())

    def reset_hard(self, ref: str) -> None:
        self.run("reset", "-q", "--hard", ref)

    def clean(self, *paths: str) -> None:
        """Remove untracked, non-ignored files under the given paths."""
        self.run("clean", "-q", "-fd", "--", *paths)
