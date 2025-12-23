"""
Progress Monitoring System
==========================

Real-time monitoring, logging, and error tracking for the autonomous coding agent.
Provides detailed visibility into what the agent is doing and what went wrong.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


class EventType(Enum):
    """Types of events that can occur during agent execution."""
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TEXT_OUTPUT = "text_output"
    ERROR = "error"
    WARNING = "warning"
    PROGRESS_UPDATE = "progress_update"
    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"


@dataclass
class AgentEvent:
    """A single event from the agent execution."""
    timestamp: str
    event_type: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass 
class SessionSummary:
    """Summary of a single agent session."""
    session_num: int
    session_type: str  # "initializer" or "coding"
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: float = 0
    tools_used: List[str] = field(default_factory=list)
    files_created: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    status: str = "running"  # running, completed, error
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProgressMonitor:
    """
    Real-time progress monitor for the autonomous agent.
    
    Features:
    - Real-time streaming output with timestamps
    - Session logging to file
    - Error detection and tracking
    - Progress summary on demand
    """
    
    def __init__(self, project_dir: Path, verbose: bool = True):
        self.project_dir = project_dir
        self.verbose = verbose
        # Store logs in .harness/logs/ to separate from app code
        self.harness_dir = project_dir / ".harness"
        self.log_dir = self.harness_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Current session tracking
        self.current_session: Optional[SessionSummary] = None
        self.events: List[AgentEvent] = []
        self.all_sessions: List[SessionSummary] = []
        
        # Error patterns to detect
        self.error_patterns = [
            (r"Execution failed: (.+)", "execution_error"),
            (r"Error: (.+)", "general_error"),
            (r"TypeError: (.+)", "type_error"),
            (r"SyntaxError: (.+)", "syntax_error"),
            (r"Expected ',' or '\}' after property value in JSON", "json_parse_error"),
            (r"ENOENT: no such file or directory", "file_not_found"),
            (r"Permission denied", "permission_error"),
            (r"Timeout", "timeout_error"),
            (r"❌ ERROR: (.+)", "agent_reported_error"),
        ]
        
        # Tool use patterns
        self.tool_patterns = [
            (r"✓ (List directory|Read|Write|Edit|Create|Glob|Grep|Bash) (.+)", "builtin"),
            (r"\[Tool: (\w+)\]", "tool_marker"),
            (r"Created? (.+\.\w+)", "file_create"),
            (r"Edit(?:ed|ing)? (.+\.\w+)", "file_edit"),
        ]
        
        # Activity status patterns (for frequent updates)
        self.status_patterns = [
            (r"📍 STATUS: (.+)", "status_update"),
            (r"🔄 RETRY (\d+)/(\d+): (.+)", "retry_attempt"),
            (r"⏭️ SKIPPING: (.+)", "feature_skipped"),
        ]
        
        # Log file for this run
        self.run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"run_{self.run_timestamp}.log"
        self.json_log_file = self.log_dir / f"run_{self.run_timestamp}.json"
        
        # Initialize log file
        self._write_log(f"=== Autonomous Agent Run Started: {self.run_timestamp} ===\n")
        self._write_log(f"Project directory: {project_dir.resolve()}\n\n")
    
    def _write_log(self, text: str) -> None:
        """Write to the log file."""
        with open(self.log_file, "a") as f:
            f.write(text)
    
    def _save_json_log(self) -> None:
        """Save structured JSON log."""
        data = {
            "run_timestamp": self.run_timestamp,
            "project_dir": str(self.project_dir.resolve()),
            "sessions": [s.to_dict() for s in self.all_sessions],
            "events": [e.to_dict() for e in self.events],
            "summary": self.get_summary(),
        }
        with open(self.json_log_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def start_session(self, session_num: int, is_initializer: bool) -> None:
        """Start tracking a new session."""
        session_type = "initializer" if is_initializer else "coding"
        self.current_session = SessionSummary(
            session_num=session_num,
            session_type=session_type,
            start_time=datetime.now().isoformat(),
        )
        
        self._log_event(
            EventType.SESSION_START,
            f"Session {session_num} started ({session_type})",
            {"session_type": session_type}
        )
        
        header = f"\n{'='*70}\n  SESSION {session_num}: {session_type.upper()}\n{'='*70}\n"
        self._write_log(header)
        if self.verbose:
            print(header)
    
    def end_session(self, status: str = "completed") -> None:
        """End the current session."""
        if self.current_session:
            self.current_session.end_time = datetime.now().isoformat()
            start = datetime.fromisoformat(self.current_session.start_time)
            end = datetime.fromisoformat(self.current_session.end_time)
            self.current_session.duration_seconds = (end - start).total_seconds()
            self.current_session.status = status
            
            self._log_event(
                EventType.SESSION_END,
                f"Session {self.current_session.session_num} ended ({status})",
                {
                    "duration": self.current_session.duration_seconds,
                    "tools_used": len(self.current_session.tools_used),
                    "errors": len(self.current_session.errors),
                }
            )
            
            self.all_sessions.append(self.current_session)
            self._save_json_log()
            
            # Print session summary
            self._print_session_summary()
            self.current_session = None
    
    def _print_session_summary(self) -> None:
        """Print a summary of the just-completed session."""
        if not self.current_session:
            return
        
        s = self.current_session
        summary = f"""
--- Session {s.session_num} Summary ---
Duration: {s.duration_seconds:.1f}s
Tools used: {len(s.tools_used)} ({', '.join(list(set(s.tools_used))[:5])}{'...' if len(set(s.tools_used)) > 5 else ''})
Files created: {len(s.files_created)}
Files modified: {len(s.files_modified)}
Errors: {len(s.errors)}
Warnings: {len(s.warnings)}
Status: {s.status}
"""
        self._write_log(summary)
        if self.verbose:
            print(summary)
        
        # Show errors if any
        if s.errors:
            error_text = "\n⚠️  ERRORS IN THIS SESSION:\n"
            for i, err in enumerate(s.errors[:5], 1):
                error_text += f"  {i}. {err[:200]}{'...' if len(err) > 200 else ''}\n"
            if len(s.errors) > 5:
                error_text += f"  ... and {len(s.errors) - 5} more errors\n"
            self._write_log(error_text)
            if self.verbose:
                print(error_text)
    
    def process_line(self, line: str) -> None:
        """Process a single line of output from the agent."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Write raw line to log
        self._write_log(f"[{timestamp}] {line}\n")
        
        # Check for status updates (activity tracking)
        for pattern, status_type in self.status_patterns:
            match = re.search(pattern, line)
            if match:
                if status_type == "status_update":
                    status_msg = match.group(1)
                    self._log_event(EventType.TEXT_OUTPUT, f"Activity: {status_msg}", {"type": "status"})
                    if self.verbose:
                        print(f"  📍 {status_msg}")
                    return  # Don't process further
                elif status_type == "retry_attempt":
                    attempt, max_attempts, msg = match.group(1), match.group(2), match.group(3)
                    self._log_event(EventType.WARNING, f"Retry {attempt}/{max_attempts}: {msg}", {"type": "retry"})
                    if self.current_session:
                        self.current_session.warnings.append(f"Retry {attempt}/{max_attempts}: {msg}")
                    if self.verbose:
                        print(f"  🔄 Retry {attempt}/{max_attempts}: {msg}")
                    return
                elif status_type == "feature_skipped":
                    skip_msg = match.group(1)
                    self._log_event(EventType.WARNING, f"Skipped: {skip_msg}", {"type": "skip"})
                    if self.current_session:
                        self.current_session.warnings.append(f"Skipped: {skip_msg}")
                    if self.verbose:
                        print(f"  ⏭️ {skip_msg}")
                    return
        
        # Check for errors
        for pattern, error_type in self.error_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                error_msg = match.group(1) if match.lastindex else line
                self._log_event(EventType.ERROR, error_msg, {"error_type": error_type})
                if self.current_session:
                    self.current_session.errors.append(f"[{error_type}] {error_msg}")
                if self.verbose:
                    print(f"\n❌ ERROR [{error_type}]: {error_msg[:100]}")
        
        # Check for tool use
        for pattern, tool_type in self.tool_patterns:
            match = re.search(pattern, line)
            if match:
                if tool_type == "builtin":
                    tool_name = match.group(1)
                    target = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
                    self._log_event(EventType.TOOL_USE, f"{tool_name}: {target}", {"tool": tool_name})
                    if self.current_session:
                        self.current_session.tools_used.append(tool_name)
                elif tool_type == "tool_marker":
                    tool_name = match.group(1)
                    self._log_event(EventType.TOOL_USE, tool_name, {"tool": tool_name})
                    if self.current_session:
                        self.current_session.tools_used.append(tool_name)
                elif tool_type == "file_create":
                    filename = match.group(1)
                    self._log_event(EventType.FILE_CREATED, filename, {"file": filename})
                    if self.current_session:
                        self.current_session.files_created.append(filename)
                elif tool_type == "file_edit":
                    filename = match.group(1)
                    self._log_event(EventType.FILE_MODIFIED, filename, {"file": filename})
                    if self.current_session:
                        self.current_session.files_modified.append(filename)
        
        # Print with timestamp if verbose
        if self.verbose:
            # Color code based on content
            if "✓" in line:
                print(f"  ✓ {line.replace('✓', '').strip()}")
            elif "Error" in line or "failed" in line.lower():
                print(f"  ❌ {line}")
            elif "[Tool:" in line:
                print(f"  🔧 {line}")
            else:
                print(line, end="" if line.endswith("\n") else "\n")
    
    def _log_event(self, event_type: EventType, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Log an event."""
        event = AgentEvent(
            timestamp=datetime.now().isoformat(),
            event_type=event_type.value,
            message=message,
            details=details if details is not None else {},
        )
        self.events.append(event)
    
    def log_progress_update(self, passing: int, total: int) -> None:
        """Log a progress update."""
        self._log_event(
            EventType.PROGRESS_UPDATE,
            f"Tests: {passing}/{total}",
            {"passing": passing, "total": total}
        )
        
        if total > 0:
            pct = (passing / total) * 100
            progress_bar = self._make_progress_bar(passing, total)
            msg = f"\n📊 Progress: {progress_bar} {passing}/{total} ({pct:.1f}%)\n"
        else:
            msg = "\n📊 Progress: feature_list.json not yet created\n"
        
        self._write_log(msg)
        if self.verbose:
            print(msg)
    
    def _make_progress_bar(self, current: int, total: int, width: int = 30) -> str:
        """Create a text progress bar."""
        if total == 0:
            return "[" + " " * width + "]"
        
        filled = int(width * current / total)
        empty = width - filled
        return "[" + "█" * filled + "░" * empty + "]"
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all sessions."""
        total_duration = sum(s.duration_seconds for s in self.all_sessions)
        total_errors = sum(len(s.errors) for s in self.all_sessions)
        total_tools = sum(len(s.tools_used) for s in self.all_sessions)
        all_files_created = []
        for s in self.all_sessions:
            all_files_created.extend(s.files_created)
        
        return {
            "total_sessions": len(self.all_sessions),
            "total_duration_seconds": total_duration,
            "total_errors": total_errors,
            "total_tool_calls": total_tools,
            "files_created": len(set(all_files_created)),
            "last_session_status": self.all_sessions[-1].status if self.all_sessions else "none",
        }
    
    def print_final_summary(self) -> None:
        """Print the final summary of the run."""
        summary = self.get_summary()
        
        output = f"""
{'='*70}
  RUN COMPLETE
{'='*70}

📊 Summary:
  Sessions: {summary['total_sessions']}
  Duration: {summary['total_duration_seconds']:.1f}s ({summary['total_duration_seconds']/60:.1f} min)
  Tool calls: {summary['total_tool_calls']}
  Files created: {summary['files_created']}
  Errors: {summary['total_errors']}

📁 Logs saved to:
  {self.log_file}
  {self.json_log_file}
"""
        
        # Add error summary if any
        all_errors = []
        for s in self.all_sessions:
            all_errors.extend(s.errors)
        
        if all_errors:
            output += f"""
⚠️  Error Summary ({len(all_errors)} total):
"""
            # Group errors by type
            error_types = {}
            for err in all_errors:
                match = re.match(r'\[(\w+)\]', err)
                err_type = match.group(1) if match else "unknown"
                error_types.setdefault(err_type, []).append(err)
            
            for err_type, errors in error_types.items():
                output += f"  [{err_type}]: {len(errors)} occurrences\n"
                # Show first error of each type
                first_err = errors[0].split(']', 1)[-1].strip()[:80]
                output += f"    Example: {first_err}...\n"
        
        self._write_log(output)
        print(output)
        
        # Final JSON save
        self._save_json_log()


def get_monitor(project_dir: Path, verbose: bool = True) -> ProgressMonitor:
    """Get or create a progress monitor for the project."""
    return ProgressMonitor(project_dir, verbose)
