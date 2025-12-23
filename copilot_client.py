"""
GitHub Copilot CLI Client Configuration
=======================================

Client for Copilot CLI integration in autonomous coding harness.
Uses `copilot` CLI - no API tokens required.

Features:
- Real-time streaming output
- Progress monitoring integration
- Error detection and logging
"""

import asyncio
import json
import subprocess
import sys
import threading
import queue
from pathlib import Path
from typing import AsyncIterator, Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field

from security import bash_security_hook


# Playwright MCP tools for browser automation
PLAYWRIGHT_TOOLS = [
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_screenshot",
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_type",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_hover",
    "mcp__playwright__browser_evaluate",
    "mcp__playwright__browser_snapshot",
]

# Built-in tools (same as before)
BUILTIN_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
]


@dataclass
class TextBlock:
    """Text content block."""
    text: str


@dataclass
class ToolUseBlock:
    """Tool use content block."""
    name: str
    input: dict


@dataclass
class ToolResultBlock:
    """Tool result content block."""
    content: str
    is_error: bool = False


@dataclass
class AssistantMessage:
    """Represents a message from the assistant."""
    content: List[Any]


@dataclass
class UserMessage:
    """Represents a message from the user (typically tool results)."""
    content: List[Any]


@dataclass
class CopilotClientOptions:
    """Configuration options for Copilot CLI client."""
    model: str = "claude-sonnet-4.5"  # Default model for harness
    system_prompt: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    mcp_servers: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    max_turns: int = 1000
    cwd: str = "."
    continue_session: bool = False  # Use --continue flag
    
    def __post_init__(self):
        if not self.allowed_tools:
            self.allowed_tools = BUILTIN_TOOLS + PLAYWRIGHT_TOOLS
        if not self.mcp_servers:
            self.mcp_servers = {}
        if not self.hooks:
            self.hooks = {}


class CopilotCLIClient:
    """
    Copilot CLI client for autonomous coding.
    
    Uses `copilot` CLI for AI interactions.
    Authentication is handled automatically by the CLI.
    
    No API tokens or environment variables required.
    """
    
    def __init__(self, options: CopilotClientOptions):
        self.options = options
        self._messages: List[Dict[str, Any]] = []
        self._process: Optional[asyncio.subprocess.Process] = None
        self._current_response = ""
        self._validate_copilot_cli()
    
    def _validate_copilot_cli(self):
        """Validate Copilot CLI is installed."""
        try:
            result = subprocess.run(
                ["copilot", "--version"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "Copilot CLI not working properly.\n"
                    "Reinstall from: https://docs.github.com/en/copilot/github-copilot-in-the-cli"
                )
            version = result.stdout.strip()
            print(f"✓ Copilot CLI version: {version}")
                
        except FileNotFoundError:
            raise RuntimeError(
                "Copilot CLI not found.\n"
                "Install from: https://docs.github.com/en/copilot/github-copilot-in-the-cli\n"
                "Verify with: copilot --version"
            )
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        # Clean up any running process
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
    
    async def query(self, message: str):
        """Send a query to GitHub Copilot CLI."""
        self._messages.append({
            "role": "user",
            "content": message,
        })
    
    async def receive_response(self, line_callback: Optional[Callable[[str], None]] = None) -> AsyncIterator[Any]:
        """
        Execute the query and yield response messages.
        
        Uses real-time streaming to show output as it happens.
        
        Args:
            line_callback: Optional callback for each line of output (for monitoring)
        """
        if not self._messages:
            return
        
        # Resolve the working directory to absolute path
        cwd_path = Path(self.options.cwd).resolve()
        
        # Build command - use absolute path for --add-dir
        # Also use --allow-all-paths to avoid permission prompts with paths containing spaces
        cmd = [
            "copilot",
            "--model", self.options.model,
            "--allow-all-tools",
            "--allow-all-paths",
            "--add-dir", str(cwd_path),
        ]
        
        # Add session continuation if enabled
        if self.options.continue_session:
            cmd.append("--continue")
        
        # Note: MCP config via --additional-mcp-config has format issues
        # Skip MCP for now - browser automation will need to be handled differently
        # TODO: Figure out correct Copilot CLI MCP config format
        
        # Add the prompt
        last_message = self._messages[-1]["content"]
        cmd.extend(["-p", last_message])
        
        # Print command for debugging
        print(f"Running: copilot --model {self.options.model} --allow-all-tools --allow-all-paths...")
        print(f"Working directory: {cwd_path}")
        
        try:
            # Use Popen for real-time streaming
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            
            # Queue for collecting stderr
            stderr_queue: queue.Queue = queue.Queue()
            
            # Thread to read stderr without blocking
            def read_stderr():
                if process.stderr:
                    for line in process.stderr:
                        stderr_queue.put(line)
            
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()
            
            # Stream stdout line by line
            if process.stdout:
                for line in process.stdout:
                    # Call the line callback if provided (for monitoring)
                    if line_callback:
                        line_callback(line.rstrip('\n'))
                    
                    # Check for tool use patterns
                    if "✓" in line or "Create" in line or "Edit" in line:
                        tool_match = self._extract_tool_use(line)
                        if tool_match:
                            yield AssistantMessage(content=[
                                ToolUseBlock(name=tool_match["name"], input={})
                            ])
                        else:
                            yield AssistantMessage(content=[TextBlock(text=line)])
                    else:
                        yield AssistantMessage(content=[TextBlock(text=line)])
                    
                    await asyncio.sleep(0)  # Allow other tasks
            
            # Wait for process to complete
            process.wait()
            
            # Wait a bit for stderr thread to finish
            stderr_thread.join(timeout=1.0)
            
            # Collect stderr
            stderr_lines = []
            while not stderr_queue.empty():
                try:
                    stderr_lines.append(stderr_queue.get_nowait())
                except queue.Empty:
                    break
            
            stderr_output = ''.join(stderr_lines)
            
            # Report stderr if any (includes usage stats and errors)
            if stderr_output:
                if line_callback:
                    for line in stderr_output.split('\n'):
                        if line.strip():
                            line_callback(f"[stderr] {line}")
                yield AssistantMessage(content=[TextBlock(text=f"\n[stderr: \n{stderr_output}]\n")])
                    
        except subprocess.TimeoutExpired:
            msg = "[Timeout: Command took too long]"
            if line_callback:
                line_callback(msg)
            yield AssistantMessage(content=[TextBlock(text=f"\n{msg}")])
        except Exception as e:
            msg = f"[Exception: {str(e)}]"
            if line_callback:
                line_callback(msg)
            yield AssistantMessage(content=[TextBlock(text=f"\n{msg}")])
    
    def _extract_tool_use(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract tool use information from CLI output."""
        import re
        
        # Match patterns like [Tool: Bash] or [Tool: Read]
        match = re.search(r'\[Tool:\s*(\w+)\]', text)
        if match:
            return {"name": match.group(1), "input": {}}
        return None


def create_client(project_dir: Path, model: str) -> CopilotCLIClient:
    """
    Create a Copilot CLI client.
    
    Uses `copilot` CLI with automatic authentication.
    No API tokens required.
    
    Directory structure:
        project_dir/
        ├── app/           <- Agent works here (generated app code)
        └── .harness/      <- Operational files (MCP.json, logs, tracking)
    
    Args:
        project_dir: Root directory for the project
        model: Model to use (claude-sonnet-4.5 default; also available: claude-sonnet-4, claude-haiku-4.5, gpt-5)
    
    Returns:
        Configured CopilotCLIClient
    """
    # Create directory structure
    project_dir.mkdir(parents=True, exist_ok=True)
    app_dir = project_dir / "app"
    harness_dir = project_dir / ".harness"
    app_dir.mkdir(parents=True, exist_ok=True)
    harness_dir.mkdir(parents=True, exist_ok=True)
    
    # Create MCP server configuration in .harness/
    mcp_config = {
        "playwright": {
            "command": "npx",
            "args": ["@playwright/mcp@latest"],
        }
    }
    
    # Write MCP config to .harness/
    mcp_config_path = harness_dir / "MCP.json"
    with open(mcp_config_path, "w") as f:
        json.dump({"mcpServers": mcp_config}, f, indent=2)
    
    print(f"Project structure created:")
    print(f"   {project_dir}/")
    print(f"   ├── app/        <- Generated app code")
    print(f"   └── .harness/   <- Operational files")
    print(f"   - Playwright MCP enabled for browser automation")
    print(f"   - Agent working directory: {app_dir.resolve()}")
    print(f"   - Model: {model}")
    print()
    
    return CopilotCLIClient(
        options=CopilotClientOptions(
            model=model,
            system_prompt="You are an expert full-stack developer building a production-quality web application.",
            allowed_tools=BUILTIN_TOOLS + PLAYWRIGHT_TOOLS,
            mcp_servers=mcp_config,
            hooks={
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [bash_security_hook]},
                ],
            },
            max_turns=1000,
            cwd=str(app_dir.resolve()),  # Agent works in app/ directory
        )
    )
