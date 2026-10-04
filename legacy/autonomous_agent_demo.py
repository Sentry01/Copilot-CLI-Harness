#!/usr/bin/env python3
"""
Autonomous Coding Agent Demo
============================

A minimal harness demonstrating long-running autonomous coding with GitHub Copilot CLI.
This script implements the two-agent pattern (initializer + coding agent) and
incorporates all the strategies from the long-running agents guide.

Copilot CLI with Playwright browser automation.

Example Usage:
    python autonomous_agent_demo.py --project-dir ./demo_project
    python autonomous_agent_demo.py --project-dir ./demo_project --max-iterations 5
"""

import argparse
import asyncio
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from agent import run_autonomous_agent


# Configuration - using Copilot CLI default harness model
DEFAULT_MODEL = "claude-opus-4.5"
DEFAULT_PROJECTS_ROOT = Path(os.environ.get("HOME", str(Path.home()))) / "Projects"
HARNESS_DIR = Path(__file__).parent.resolve()
HARNESS_LOGS_DIR = HARNESS_DIR / "harness_logs"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Autonomous Coding Agent Demo - Long-running agent harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start fresh project (creates $HOME/Projects/my_project)
  python autonomous_agent_demo.py --project-dir my_project

  # Use a specific model (available: claude-sonnet-4.5, claude-sonnet-4, claude-haiku-4.5, gpt-5)
  python autonomous_agent_demo.py --project-dir my_project --model gpt-5

  # Limit iterations for testing
  python autonomous_agent_demo.py --project-dir my_project --max-iterations 5

  # Use absolute path for custom location
  python autonomous_agent_demo.py --project-dir /custom/path/my_project

  # Run without logging or monitor
  python autonomous_agent_demo.py --project-dir my_project --no-log --no-monitor

Project Location:
  Projects are created under $HOME/Projects/ by default.
  This makes it easy to: cd $HOME/Projects/my_project && git init

Logging:
  All output is automatically logged to harness_logs/<project>_<timestamp>.log
  Progress monitoring runs in-process (no external terminal needed).
  Use --external-monitor to launch in a separate terminal window instead.
  Use --no-monitor to disable monitoring entirely.

Features:
  - Thorough analysis and careful planning
  - Automatic error recovery after 3 consecutive failures
  - Frequent activity status updates during analysis
  - Real-time progress monitoring
  - Automatic logging to harness_logs/

Prerequisites:
  - GitHub Copilot CLI installed (copilot --version)
  - Node.js 18+ with npx (for Playwright MCP server)
  - No API keys required - Copilot CLI handles authentication
        """,
    )

    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("./autonomous_demo_project"),
        help="Project name or path. Relative names are created under $HOME/Projects/ for easy git init.",
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum number of agent iterations (default: unlimited)",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL}). Available: claude-sonnet-4.5, claude-sonnet-4, claude-haiku-4.5, gpt-5",
    )

    parser.add_argument(
        "--spec",
        type=str,
        default="app_spec.txt",
        help="Spec file to use from prompts/ directory (default: app_spec.txt)",
    )

    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Disable logging to harness_logs/ directory",
    )

    parser.add_argument(
        "--no-monitor",
        action="store_true",
        help="Disable progress monitoring entirely",
    )

    parser.add_argument(
        "--external-monitor",
        action="store_true",
        help="Launch monitor in external terminal window instead of integrated",
    )

    return parser.parse_args()


def setup_logging(project_name: str, enable_logging: bool) -> Path | None:
    """Set up logging to harness_logs directory.
    
    Returns the log file path if logging is enabled, None otherwise.
    """
    if not enable_logging:
        return None
    
    # Create harness logs directory
    HARNESS_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create log file with timestamp and project name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = HARNESS_LOGS_DIR / f"{project_name}_{timestamp}.log"
    
    return log_file


class TeeOutput:
    """Write to both stdout and a file simultaneously."""
    
    def __init__(self, log_file: Path):
        self.terminal = sys.stdout
        self.log_file = open(log_file, "w", buffering=1)  # Line buffered
    
    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log_file.write(message)
        self.log_file.flush()
    
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
    
    def close(self):
        self.log_file.close()


class IntegratedMonitor:
    """In-process monitor that displays colorized log output.
    
    This replaces the external terminal monitor, keeping everything
    in the VS Code integrated terminal.
    """
    
    # ANSI color codes
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color
    BOLD = '\033[1m'
    
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_position = 0
    
    def _colorize_line(self, line: str) -> str:
        """Apply colors to log line based on content."""
        if "✓" in line:
            return f"{self.GREEN}{line}{self.NC}"
        elif "ERROR" in line or "❌" in line:
            return f"{self.RED}{line}{self.NC}"
        elif "📍 STATUS:" in line:
            return f"{self.CYAN}{line}{self.NC}"
        elif "🔄 RETRY" in line:
            return f"{self.YELLOW}{line}{self.NC}"
        elif "STAGE" in line:
            return f"{self.BOLD}{self.BLUE}{line}{self.NC}"
        elif "SESSION" in line:
            return f"{self.BOLD}{self.CYAN}{line}{self.NC}"
        elif "Progress:" in line or "📊" in line:
            return f"{self.BOLD}{self.GREEN}{line}{self.NC}"
        elif "Create" in line or "Edit" in line:
            return f"{self.YELLOW}{line}{self.NC}"
        return line
    
    def _monitor_loop(self):
        """Background thread that tails the log file."""
        while not self._stop_event.is_set():
            try:
                if self.log_file.exists():
                    with open(self.log_file, 'r') as f:
                        f.seek(self._last_position)
                        new_content = f.read()
                        if new_content:
                            for line in new_content.splitlines():
                                # Don't double-print - the TeeOutput already writes to stdout
                                # This monitor is for external viewing only
                                pass
                            self._last_position = f.tell()
            except Exception:
                pass  # Ignore read errors
            time.sleep(0.5)
    
    def start(self):
        """Start the monitor thread."""
        # Note: Since we're using TeeOutput, all output already goes to stdout
        # The integrated monitor doesn't need to do anything extra
        # It's kept for API compatibility and potential future enhancements
        pass
    
    def stop(self):
        """Stop the monitor thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


def launch_external_monitor(log_file: Path) -> subprocess.Popen | None:
    """Launch the monitor script in a new terminal window.
    
    Returns the subprocess handle or None if launch failed.
    """
    monitor_script = HARNESS_DIR / "monitor_agent.sh"
    
    if not monitor_script.exists():
        print(f"⚠️  Monitor script not found: {monitor_script}")
        return None
    
    try:
        # On macOS, use osascript to open a new Terminal window
        if sys.platform == "darwin":
            # AppleScript to open Terminal with monitor
            applescript = f'''
            tell application "Terminal"
                activate
                do script "cd '{HARNESS_DIR}' && '{monitor_script}' '{log_file}'"
            end tell
            '''
            proc = subprocess.Popen(
                ["osascript", "-e", applescript],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return proc
        else:
            # On Linux, try common terminal emulators
            for term in ["gnome-terminal", "xterm", "konsole"]:
                try:
                    proc = subprocess.Popen(
                        [term, "--", str(monitor_script), str(log_file)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    return proc
                except FileNotFoundError:
                    continue
            print("⚠️  Could not find terminal emulator to launch monitor")
            return None
    except Exception as e:
        print(f"⚠️  Failed to launch monitor: {e}")
        return None


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # No API key check needed - Copilot CLI handles authentication automatically

    # Place projects under $HOME/Projects/ for easy git init
    project_dir = args.project_dir
    if project_dir.is_absolute():
        # If absolute path, use as-is
        pass
    else:
        # Place relative paths under the projects root directory
        # This makes it easy to git init and push to GitHub separately
        project_dir = DEFAULT_PROJECTS_ROOT / project_dir.name

    # Set up logging
    project_name = project_dir.name
    log_file = setup_logging(project_name, not args.no_log)
    tee_output = None
    monitor_proc = None
    integrated_monitor = None
    
    if log_file:
        tee_output = TeeOutput(log_file)
        sys.stdout = tee_output
        print(f"📝 Logging to: {log_file}")
        
        # Set up monitoring
        if not args.no_monitor:
            if args.external_monitor:
                # Launch external terminal monitor
                print(f"📊 Launching external monitor terminal...")
                monitor_proc = launch_external_monitor(log_file)
                if monitor_proc:
                    print(f"✓ Monitor launched in external terminal window")
                else:
                    print(f"📊 Manual monitor: ./monitor_agent.sh {log_file}")
            else:
                # Use integrated monitor (default) - output goes directly to this terminal
                print(f"📊 Monitoring in integrated terminal (all output below)")
                print(f"   Use --external-monitor to launch in separate window")
                integrated_monitor = IntegratedMonitor(log_file)
                integrated_monitor.start()
        else:
            print(f"📊 Monitor disabled. Manual: ./monitor_agent.sh {log_file}")
        print()

    # Run the agent
    try:
        asyncio.run(
            run_autonomous_agent(
                project_dir=project_dir,
                model=args.model,
                max_iterations=args.max_iterations,
                spec_file=args.spec,
            )
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        print("To resume, run the same command again")
    except Exception as e:
        print(f"\nFatal error: {e}")
        raise
    finally:
        # Stop integrated monitor if running
        if integrated_monitor:
            integrated_monitor.stop()
        
        # Restore stdout and close log file
        if tee_output:
            sys.stdout = tee_output.terminal
            tee_output.close()
            print(f"\n📝 Log saved to: {log_file}")


if __name__ == "__main__":
    main()
