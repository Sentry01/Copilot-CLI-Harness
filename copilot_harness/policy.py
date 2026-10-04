"""Permission policy for agent sessions.

Pure decision logic, independent of the Copilot SDK so it can be unit-tested. The SDK
backend maps each ``PermissionRequest`` variant onto one of these checks, using the
CLI's own shell parser output (command identifiers, read-only flags, the paths and URLs
a command may touch). Hand-parsing shell strings is what made the v1 allowlist bypassable.

The rules are layered:
1. Hard denials (privilege escalation, remote shells, publishing, cloud CLIs, GitHub writes).
2. Git is read-only for agents; the harness owns commits.
3. Writes are default-deny outside the role's scope; frozen tests can never be written.
4. Network: localhost plus a small documentation/registry allowlist.
5. Secrets: credential stores are unreadable even with read-only commands.

This is defence in depth, not a sandbox. Run the harness in a container or VM for
real isolation; after every session the harness also checks the git working tree and
restores anything changed outside the role's scope.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROLE_WRITE_SCOPES: dict[str, tuple[str, ...]] = {
    "analyst": ("harness/requirements.json",),
    "architect": ("harness/contract.json", "harness/app-contract.json", "app/**"),
    "test_architect": ("harness/test_plan.json",),
    "plan_reviewer": (".harness/reviews/**",),
    "test_author": ("acceptance/specs/**",),
    "coder": ("app/**", "harness/app-contract.json", ".harness/disputes/claims/**"),
    "adjudicator": (".harness/disputes/decisions/**",),
}
# Generated/cache output that any role may produce as a side effect of running tools.
ALWAYS_WRITABLE = (
    "**/node_modules/**",
    "acceptance/test-results/**",
    "acceptance/playwright-report/**",
    "acceptance/blob-report/**",
    "acceptance/reports/**",
    ".harness/scratch/**",
)

DENIED_COMMANDS = {
    # privilege escalation / remote access
    "sudo", "su", "doas", "pkexec", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat",
    "telnet", "ftp", "socat",
    # system administration
    "mkfs", "dd", "fdisk", "mount", "umount", "shutdown", "reboot", "halt", "systemctl",
    "launchctl", "crontab", "chown", "useradd", "usermod", "passwd", "iptables",
    # code/eval escape hatches
    "eval",
    # GitHub / cloud / infra side effects
    "gh", "hub", "aws", "gcloud", "gsutil", "az", "kubectl", "helm", "terraform", "pulumi",
    "docker", "podman", "flyctl", "vercel", "netlify", "heroku",
    # the agent must not drive Copilot itself
    "copilot",
}
DENIED_SUBCOMMANDS = {
    "npm": {"publish", "adduser", "login", "logout", "token", "owner", "deprecate", "unpublish", "access"},
    "pnpm": {"publish", "login", "logout"},
    "yarn": {"publish", "login", "logout", "npm"},
    "pip": {"upload"},
    "twine": {"upload", "register"},
    "cargo": {"publish", "login"},
}
# Agents may inspect history and stage nothing else; the harness commits green states.
GIT_READ_ONLY = {
    "status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "grep", "describe",
    "shortlog", "cat-file", "ls-tree", "reflog", "help", "--version", "version",
}
NETWORK_COMMANDS = {"curl", "wget", "http", "httpie", "xh"}
# Mutating commands whose targets must be explicit literal paths (no `$VAR`, globs resolved by the CLI).
PATH_MUTATING = {"rm", "rmdir", "mv", "cp", "chmod", "truncate", "shred", "ln", "install", "tee", "unlink"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
SENSITIVE_PARTS = (
    "/.ssh/", "/.aws/", "/.gnupg/", "/.config/gh/", "/.copilot/", "/.docker/", "/.kube/",
    "/.azure/", "/.config/gcloud/", "/.netrc", "/.npmrc", "/.pypirc", "/.git-credentials",
    "/etc/shadow", "/etc/sudoers",
)


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = ""

    @staticmethod
    def ok() -> Decision:
        return Decision(True)

    @staticmethod
    def deny(reason: str) -> Decision:
        return Decision(False, reason)


@dataclass(frozen=True)
class ShellCommand:
    identifier: str  # e.g. "git push", "npm", "ls"
    read_only: bool
    text: str = ""  # the segment's full text, when available


def _match(rel: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        if prefix.startswith("**/"):
            return f"/{prefix[3:]}/" in f"/{rel}/"
        return rel == prefix or rel.startswith(prefix + "/")
    return rel == pattern


@dataclass
class Policy:
    root: Path
    role: str
    allow_urls: Sequence[str] = ()
    allow_git_push: bool = False
    extra_allowed_commands: Iterable[str] = ()
    extra_denied_commands: Iterable[str] = ()
    mcp_servers: Iterable[str] = ("playwright",)
    extra_write_scope: Sequence[str] = ()
    _temp_roots: tuple[Path, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        temps = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
        self._temp_roots = tuple(sorted(temps))
        self._denied = (DENIED_COMMANDS | set(self.extra_denied_commands)) - set(self.extra_allowed_commands)

    @property
    def denied_commands(self) -> frozenset[str]:
        """Effective command denylist (defaults + configured denials - configured allowances)."""
        return frozenset(self._denied)

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    @property
    def write_scope(self) -> tuple[str, ...]:
        return (*ROLE_WRITE_SCOPES.get(self.role, ()), *self.extra_write_scope, *ALWAYS_WRITABLE)

    def resolve(self, path: str, cwd: str | None = None) -> Path:
        """Absolute path with symlinks resolved, so a link inside a writable scope cannot reach outside it."""
        p = Path(os.path.expanduser(path))
        if not p.is_absolute():
            p = Path(cwd or self.root) / p
        return Path(os.path.realpath(p))

    def relative(self, p: Path) -> str | None:
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return None

    def is_sensitive(self, p: Path) -> bool:
        s = p.as_posix() + ("/" if p.is_dir() else "")
        return any(part in s or s.endswith(part.rstrip("/")) for part in SENSITIVE_PARTS)

    def in_temp(self, p: Path) -> bool:
        return any(p == t or t in p.parents for t in self._temp_roots)

    def can_write_rel(self, rel: str) -> bool:
        if rel.startswith(".git/") or rel == ".git":
            return False
        return any(_match(rel, pat) for pat in self.write_scope)

    def check_write(self, path: str, cwd: str | None = None) -> Decision:
        p = self.resolve(path, cwd)
        if self.is_sensitive(p):
            return Decision.deny(f"writing to credential store {p} is not allowed")
        rel = self.relative(p)
        if rel is None:
            # Outside the project: only the system temp dir is writable.
            if self.in_temp(p):
                return Decision.ok()
            return Decision.deny(f"{p} is outside the project; only paths inside {self.root} may be written")
        if rel == "" or not self.can_write_rel(rel):
            scope = ", ".join(ROLE_WRITE_SCOPES.get(self.role, ())) or "nothing"
            return Decision.deny(f"the {self.role} role may not write {rel or '.'}; it may write: {scope}")
        return Decision.ok()

    def check_read(self, path: str, cwd: str | None = None) -> Decision:
        p = self.resolve(path, cwd)
        if self.is_sensitive(p):
            return Decision.deny(f"reading credential store {p} is not allowed")
        return Decision.ok()

    # ------------------------------------------------------------------
    # network
    # ------------------------------------------------------------------
    def check_url(self, url: str) -> Decision:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.hostname or "").lower()
        if host in LOCAL_HOSTS:
            return Decision.ok()
        for allowed in self.allow_urls:
            a = allowed.lower().removeprefix("https://").removeprefix("http://").strip("/")
            if host == a or host.endswith("." + a):
                return Decision.ok()
        return Decision.deny(f"network access to {host or url} is not allowed (localhost and documentation hosts only)")

    # ------------------------------------------------------------------
    # MCP
    # ------------------------------------------------------------------
    def check_mcp(self, server: str, tool: str, args: Any = None) -> Decision:
        if server not in set(self.mcp_servers):
            return Decision.deny(f"MCP server {server!r} (tool {tool}) is not enabled for harness sessions")
        # Browser tools must stay on the app under test / allowed hosts (e.g. browser_navigate).
        for url in _urls_in(args):
            if not url.lower().startswith(("http://", "https://")) and "://" in url:
                return Decision.deny(f"MCP tool {tool} may not open {url.split('://', 1)[0]}:// URLs")
            d = self.check_url(url)
            if not d.allow:
                return d
        return Decision.ok()

    # ------------------------------------------------------------------
    # shell
    # ------------------------------------------------------------------
    def check_shell(
        self,
        commands: Sequence[ShellCommand],
        full_text: str = "",
        possible_paths: Sequence[str] = (),
        possible_urls: Sequence[str] = (),
        has_write_redirection: bool = False,
        sandbox_bypass: bool = False,
        cwd: str | None = None,
    ) -> Decision:
        if sandbox_bypass:
            return Decision.deny("sandbox bypass requests are not allowed")
        if not commands:
            return Decision.deny("could not determine which commands this shell invocation runs")

        all_read_only = all(c.read_only for c in commands) and not has_write_redirection
        for cmd in commands:
            d = self._check_command(cmd, possible_urls)
            if not d.allow:
                return d
        mutating = {os.path.basename(c.identifier.split()[0]) for c in commands if c.identifier.strip()} & PATH_MUTATING
        if mutating and not possible_paths:
            return Decision.deny(f"`{sorted(mutating)[0]}` needs explicit literal paths so the harness can check them")
        if not all_read_only and any(ch in path for path in possible_paths for ch in "$`"):
            return Decision.deny("commands that modify files must use literal paths, not shell variables")

        for path in possible_paths:
            p = self.resolve(path, cwd)
            if self.is_sensitive(p):
                return Decision.deny(f"{path} is a credential store and may not be accessed")
            if all_read_only:
                continue
            rel = self.relative(p)
            if rel is None:
                if self.in_temp(p):
                    continue
                return Decision.deny(f"command may modify {p}, which is outside the project")
            if rel and not self.can_write_rel(rel) and not self._is_parent_of_scope(rel):
                return Decision.deny(
                    f"command may modify {rel}, which the {self.role} role may not change "
                    f"(frozen tests and harness records are read-only)"
                )

        for url in possible_urls:
            d = self.check_url(url)
            if not d.allow:
                return d
        return Decision.ok()

    def _is_parent_of_scope(self, rel: str) -> bool:
        """A directory argument like `app` or `.` for e.g. `npm --prefix app install`."""
        return any(pat.startswith(rel.rstrip("/") + "/") for pat in ROLE_WRITE_SCOPES.get(self.role, ()))

    def _check_command(self, cmd: ShellCommand, possible_urls: Sequence[str]) -> Decision:
        ident = cmd.identifier.strip()
        words = ident.split()
        if not words:
            return Decision.deny("empty command")
        base = os.path.basename(words[0])
        if base in self._denied:
            return Decision.deny(f"`{base}` is not allowed in harness sessions")
        sub = words[1] if len(words) > 1 else _subcommand_from_text(cmd.text, base)
        if base == "git":
            if sub == "push" and self.allow_git_push:
                return Decision.ok()
            if sub not in GIT_READ_ONLY:
                return Decision.deny(
                    f"`git {sub or ''}` is not allowed: git is read-only for agents; the harness commits verified work"
                )
        if base in DENIED_SUBCOMMANDS and sub in DENIED_SUBCOMMANDS[base]:
            return Decision.deny(f"`{base} {sub}` is not allowed in harness sessions")
        if base in NETWORK_COMMANDS:
            if not possible_urls:
                return Decision.deny(f"`{base}` may only target explicit localhost URLs")
            for url in possible_urls:
                d = self.check_url(url)
                if not d.allow:
                    return d
        return Decision.ok()


def _urls_in(args: Any) -> list[str]:
    """URL-like strings in MCP tool arguments: any `url` field, and any value containing '://'."""
    found: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, str(k))
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and (key.lower() == "url" or "://" in value):
            found.append(value.strip())

    walk(args)
    return found


_GIT_GLOBAL_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def _subcommand_from_text(text: str, base: str) -> str | None:
    """Best-effort first positional argument after ``base`` (skipping git global options)."""
    if not text:
        return None
    try:
        tokens = shlex.split(text)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) == base:
            rest = tokens[i + 1 :]
            j = 0
            while j < len(rest):
                t = rest[j]
                if t in _GIT_GLOBAL_OPTS_WITH_ARG:
                    j += 2
                    continue
                if t.startswith("-"):
                    j += 1
                    continue
                return t
            return None
    return None
