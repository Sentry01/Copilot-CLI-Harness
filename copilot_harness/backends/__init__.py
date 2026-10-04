from copilot_harness.backends.base import AgentBackend, SessionOutcome, SessionSpec
from copilot_harness.config import HarnessConfig


def make_backend(cfg: HarnessConfig, verbose: bool = True) -> AgentBackend:
    if cfg.backend.kind == "cli":
        from copilot_harness.backends.cli import CliBackend

        return CliBackend(cfg.backend.cli_path, cfg.security.allow_git_push, verbose)
    from copilot_harness.backends.sdk import SdkBackend

    return SdkBackend(cfg.backend.cli_path, verbose)


__all__ = ["AgentBackend", "SessionOutcome", "SessionSpec", "make_backend"]
