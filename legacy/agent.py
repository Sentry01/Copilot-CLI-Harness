"""
Agent Session Logic
===================

Core agent interaction functions for running autonomous coding sessions.
Migrated to use GitHub Copilot CLI with Playwright for browser automation.

Features:
- Real-time progress monitoring
- Detailed error tracking
- Session logging
"""

import asyncio
from pathlib import Path
from typing import Optional

from copilot_client import CopilotCLIClient, create_client
from progress import count_passing_tests
from prompts import get_initializer_prompt, get_coding_prompt, copy_spec_to_project
from monitor import ProgressMonitor, get_monitor


# Configuration
AUTO_CONTINUE_DELAY_SECONDS = 3
MAX_ERROR_RETRIES = 3  # Maximum retries before escalating


async def run_agent_session(
    client: CopilotCLIClient,
    message: str,
    project_dir: Path,
    monitor: ProgressMonitor,
) -> tuple[str, str, int]:
    """
    Run a single agent session using Copilot CLI.

    Args:
        client: Copilot CLI client
        message: The prompt to send
        project_dir: Project directory path
        monitor: Progress monitor for logging

    Returns:
        (status, response_text, error_count) where status is:
        - "continue" if agent should continue working
        - "error" if an error occurred
        - "complete" if all tests passing
    """
    print("Sending prompt to Copilot CLI...\n")

    try:
        # Send the query
        await client.query(message)

        # Collect response text and show tool use
        response_text = ""
        error_count = 0
        files_created = 0
        
        async for msg in client.receive_response(line_callback=monitor.process_line):
            msg_type = type(msg).__name__

            # Handle AssistantMessage (text and tool use)
            if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "TextBlock" and hasattr(block, "text"):
                        response_text += block.text
                        # Check for errors in the text
                        if "error" in block.text.lower() or "failed" in block.text.lower():
                            error_count += 1
                        # Track file creation
                        if "create" in block.text.lower() or "+)" in block.text:
                            files_created += 1
                    elif block_type == "ToolUseBlock" and hasattr(block, "name"):
                        pass  # Tool use is logged by monitor

            # Handle UserMessage (tool results)
            elif msg_type == "UserMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "ToolResultBlock":
                        result_content = getattr(block, "content", "")
                        is_error = getattr(block, "is_error", False)

                        # Check if command was blocked by security hook
                        if "blocked" in str(result_content).lower():
                            print(f"   [BLOCKED] {result_content}", flush=True)
                        elif is_error:
                            error_count += 1

        print("\n" + "-" * 70 + "\n")
        
        # Check if all tests are passing
        passing, total = count_passing_tests(project_dir)
        if total > 0 and passing == total:
            return "complete", response_text, error_count
        
        # Determine status based on errors
        if error_count > 0:
            return "error", response_text, error_count
        return "continue", response_text, error_count

    except Exception as e:
        print(f"Error during agent session: {e}")
        return "error", str(e), 1


async def run_autonomous_agent(
    project_dir: Path,
    model: str,
    max_iterations: Optional[int] = None,
    verbose: bool = True,
    spec_file: str = "app_spec.txt",
) -> None:
    """
    Run the autonomous agent loop.

    Args:
        project_dir: Directory for the project
        model: Claude model to use
        max_iterations: Maximum number of iterations (None for unlimited)
        verbose: Print real-time output (default True)
        spec_file: Name of the spec file to use (default: app_spec.txt)
    """
    print("\n" + "=" * 70)
    print("  AUTONOMOUS CODING AGENT DEMO")
    print("=" * 70)
    print(f"\nProject directory: {project_dir}")
    print(f"Model: {model}")
    print(f"Spec file: {spec_file}")
    if max_iterations:
        print(f"Max iterations: {max_iterations}")
    else:
        print("Max iterations: Unlimited (will run until completion)")
    print()

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize progress monitor
    monitor = get_monitor(project_dir, verbose=verbose)

    # Check if this is a fresh start or continuation
    # Look for feature_list.json in .harness/ (new) or root (legacy)
    harness_dir = project_dir / ".harness"
    tests_file = harness_dir / "feature_list.json"
    if not tests_file.exists():
        tests_file = project_dir / "feature_list.json"  # Legacy fallback
    is_first_run = not tests_file.exists()

    if is_first_run:
        print("Fresh start - will use initializer agent")
        print()
        print("=" * 70)
        print("  NOTE: First session takes 10-20+ minutes!")
        print("  The agent is generating 200 detailed test cases.")
        print("  Watch the real-time output below for progress.")
        print("=" * 70)
        print()
        # Copy the app spec into the project directory for the agent to read
        copy_spec_to_project(project_dir, spec_file)
    else:
        print("Continuing existing project")
        passing, total = count_passing_tests(project_dir)
        monitor.log_progress_update(passing, total)

    # Main loop
    iteration = 0
    consecutive_errors = 0  # Track consecutive errors for improvement loop

    while True:
        iteration += 1

        # Check max iterations
        if max_iterations and iteration > max_iterations:
            print(f"\nReached max iterations ({max_iterations})")
            print("To continue, run the script again without --max-iterations")
            break

        # Start session monitoring
        monitor.start_session(iteration, is_first_run)

        # Create client (fresh context)
        client = create_client(project_dir, model)

        # Choose prompt based on session type and error state
        if is_first_run:
            prompt = get_initializer_prompt()
            is_first_run = False  # Only use initializer once
        elif consecutive_errors >= MAX_ERROR_RETRIES:
            # Error recovery mode - add recovery instructions
            base_prompt = get_coding_prompt()
            prompt = f"""⚠️ RECOVERY MODE: Previous {consecutive_errors} sessions had errors.

PRIORITY: Fix the blocking issues before continuing with new features.

1. Check progress.json for documented issues
2. Review recent git commits for broken changes
3. Run existing tests to identify what's broken
4. Fix the most critical issue first

{base_prompt}"""
            print(f"\n⚠️  Entering recovery mode after {consecutive_errors} consecutive errors\n")
        else:
            prompt = get_coding_prompt()

        # Run session with async context manager
        async with client:
            status, response, error_count = await run_agent_session(client, prompt, project_dir, monitor)

        # End session monitoring
        monitor.end_session(status)

        # Update progress
        passing, total = count_passing_tests(project_dir)
        monitor.log_progress_update(passing, total)

        # Handle status
        if status == "complete":
            print("\n🎉 ALL TESTS PASSING! Project complete!")
            break
            
        elif status == "continue":
            consecutive_errors = 0  # Reset error counter on success
            print(f"\nAgent will auto-continue in {AUTO_CONTINUE_DELAY_SECONDS}s...")
            await asyncio.sleep(AUTO_CONTINUE_DELAY_SECONDS)

        elif status == "error":
            consecutive_errors += 1
            if consecutive_errors >= MAX_ERROR_RETRIES:
                print(f"\n⚠️  {consecutive_errors} consecutive errors - entering recovery mode next session")
            else:
                print(f"\nSession had {error_count} error(s) (attempt {consecutive_errors}/{MAX_ERROR_RETRIES})")
            print("Will retry with a fresh session...")
            await asyncio.sleep(AUTO_CONTINUE_DELAY_SECONDS)

        # Small delay between sessions
        if max_iterations is None or iteration < max_iterations:
            print("\nPreparing next session...\n")
            await asyncio.sleep(1)

    # Final summary from monitor
    monitor.print_final_summary()

    # Print instructions for running the generated application
    print("\n" + "-" * 70)
    print("  TO RUN THE GENERATED APPLICATION:")
    print("-" * 70)
    print(f"\n  cd {project_dir.resolve()}")
    print("  ./init.sh           # Run the setup script")
    print("  # Or manually:")
    print("  npm install && npm run dev")
    print("\n  Then open http://localhost:3000 (or check init.sh for the URL)")
    print("-" * 70)

    print("\nDone!")
