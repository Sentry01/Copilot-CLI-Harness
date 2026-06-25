"""
Command allowlist
=================

V1 shipped this logic but **never enforced it** — ``copilot_client`` invoked the
CLI with ``--allow-all-tools`` and the security hook was attached to an options
object that was never passed to the subprocess. The README still claimed
``rm``/``curl``/``sudo`` were blocked. They were not.

In V2 this module is pure (no SDK imports) and exposes ``evaluate_command`` which
``sdk_client`` wires into a real Copilot SDK permission handler, so the allowlist
is actually applied to every shell command the agent tries to run.
"""

from __future__ import annotations

import os
import re
import shlex

# Commands an autonomous web-app coding agent legitimately needs.
ALLOWED_COMMANDS = {
    # Inspection
    "ls", "cat", "head", "tail", "wc", "grep", "find", "echo", "which", "env",
    # File ops (the agent mostly uses Read/Write/Edit tools, but these help)
    "cp", "mkdir", "chmod", "touch",
    # Directory / process
    "pwd", "cd", "ps", "lsof", "sleep", "pkill",
    # Node toolchain
    "npm", "npx", "node", "pnpm", "yarn",
    # Version control + GitHub (needed for the Issues backend)
    "git", "gh",
    # Local script
    "init.sh",
    # Browser automation
    "playwright",
}

# Commands that, even when allowlisted, get argument-level validation.
COMMANDS_NEEDING_EXTRA_VALIDATION = {"pkill", "chmod", "init.sh"}


def split_command_segments(command_string: str) -> list[str]:
    """Split a compound command into segments on ``&&``, ``||`` and ``;``."""
    segments = re.split(r"\s*(?:&&|\|\|)\s*", command_string)
    result: list[str] = []
    for segment in segments:
        for sub in re.split(r'(?<!["\'])\s*;\s*(?!["\'])', segment):
            sub = sub.strip()
            if sub:
                result.append(sub)
    return result


def extract_commands(command_string: str) -> list[str]:
    """Return the base command names invoked in a shell string.

    Handles pipes, chaining (``&&``/``||``/``;``) and shell keywords. Returns an
    empty list on a parse failure so callers can fail safe (block).
    """
    commands: list[str] = []
    segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', command_string)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return []  # malformed -> fail safe
        if not tokens:
            continue

        expect_command = True
        for token in tokens:
            if token in ("|", "||", "&&", "&"):
                expect_command = True
                continue
            if token in (
                "if", "then", "else", "elif", "fi", "for", "while", "until",
                "do", "done", "case", "esac", "in", "!", "{", "}",
            ):
                continue
            if token.startswith("-"):
                continue
            if "=" in token and not token.startswith("="):
                continue  # VAR=value assignment
            if expect_command:
                commands.append(os.path.basename(token))
                expect_command = False
    return commands


def validate_pkill_command(command_string: str) -> tuple[bool, str]:
    """Only allow killing dev processes."""
    allowed = {"node", "npm", "npx", "vite", "next", "playwright"}
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse pkill command"
    args = [t for t in tokens[1:] if not t.startswith("-")]
    if not args:
        return False, "pkill requires a process name"
    target = args[-1]
    if " " in target:
        target = target.split()[0]
    if target in allowed:
        return True, ""
    return False, f"pkill only allowed for dev processes: {sorted(allowed)}"


def validate_chmod_command(command_string: str) -> tuple[bool, str]:
    """Only allow ``chmod +x`` style execute bits, never recursive."""
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse chmod command"
    if not tokens or tokens[0] != "chmod":
        return False, "Not a chmod command"
    mode, files = None, []
    for token in tokens[1:]:
        if token.startswith("-"):
            return False, "chmod flags are not allowed"
        if mode is None:
            mode = token
        else:
            files.append(token)
    if mode is None:
        return False, "chmod requires a mode"
    if not files:
        return False, "chmod requires at least one file"
    if not re.match(r"^[ugoa]*\+x$", mode):
        return False, f"chmod only allowed with +x mode, got: {mode}"
    return True, ""


def validate_init_script(command_string: str) -> tuple[bool, str]:
    """Only allow ``./init.sh`` (optionally path-qualified)."""
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse init script command"
    if not tokens:
        return False, "Empty command"
    script = tokens[0]
    if script == "./init.sh" or script.endswith("/init.sh"):
        return True, ""
    return False, f"Only ./init.sh is allowed, got: {script}"


def _segment_for(cmd: str, segments: list[str]) -> str:
    for segment in segments:
        if cmd in extract_commands(segment):
            return segment
    return ""


def evaluate_command(command: str) -> tuple[bool, str]:
    """Decide whether a full shell command string is permitted.

    Returns ``(allowed, reason)``. ``reason`` is empty when allowed and carries a
    human-readable explanation (surfaced back to the agent) when blocked.
    """
    if not command or not command.strip():
        return True, ""  # nothing to run

    commands = extract_commands(command)
    if not commands:
        return False, f"Could not parse command for security validation: {command}"

    segments = split_command_segments(command)
    for cmd in commands:
        if cmd not in ALLOWED_COMMANDS:
            return False, f"Command '{cmd}' is not in the allowlist"
        if cmd in COMMANDS_NEEDING_EXTRA_VALIDATION:
            segment = _segment_for(cmd, segments) or command
            if cmd == "pkill":
                ok, reason = validate_pkill_command(segment)
            elif cmd == "chmod":
                ok, reason = validate_chmod_command(segment)
            else:  # init.sh
                ok, reason = validate_init_script(segment)
            if not ok:
                return False, reason
    return True, ""
