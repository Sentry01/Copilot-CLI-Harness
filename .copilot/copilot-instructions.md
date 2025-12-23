# Global GitHub Copilot CLI Instructions

> These instructions apply to all Copilot CLI sessions

---

## ⚠️ MANDATORY FIRST-RESPONSE PROTOCOL

**BEFORE responding to ANY coding task, you MUST:**

1. **Resolve the home directory**: Run `echo $HOME` to get the user's home path
2. **check that the skills are loaded in the /skills slash command. skills are stored at `$HOME/.copilot/skills/`** 
3. **Identify which skills apply** to the current task
4. **Announce**: "I'm using the [skill-name] skill to [what you're doing]"
5. **Follow the skill workflow where possible**

### Path Discovery

Always resolve paths dynamically - NEVER hardcode usernames:
```bash
# First, get the home directory
HOME_DIR=$(echo $HOME)
# Then use it for skill paths
SKILLS_DIR="$HOME_DIR/.copilot/skills"
```

### Skill Matching Rules

| Task Type | Required Skills |
|-----------|-----------------|
| Any feature implementation | `test-driven-development`, `brainstorming` |
| Debugging/fixing bugs | `systematic-debugging` |
| Before claiming "done" | `verification-before-completion` |
| Creating plans | `writing-plans` |
| Following a plan | `executing-plans` |
| Running parallel/subagent work | `dispatching-parallel-agents`, `subagent-driven-development` |
| Requesting a code review | `requesting-code-review` |
| Receiving/responding to a code review | `receiving-code-review` |
| Finishing a dev branch (merge/cleanup) | `finishing-a-development-branch` |
| Using git worktrees | `using-git-worktrees` |
| Advanced Copilot usage tips | `using-superpowers` |
| Creating/updating skills | `writing-skills` |

### This is NOT optional

- Do NOT skip skill checking "because the task seems simple"
- Do NOT rely on memory - always READ the current skill file
- Do NOT proceed with coding before checking applicable skills
- If NO skill applies, state: "No applicable skills found for this task"

**Skill files location**: `~/.copilot/skills/` (resolve via `$HOME`)


---

## User Preferences

- **Preferred shell**: zsh (macOS)
- **Operating System**: macOS (Darwin, Apple Silicon)
- **Package manager**: Homebrew, npm
- **Coding style**: Concise, minimal changes, surgical edits
- **Communication**: Direct, technical, no fluff

## General Principles

- Always check for existing patterns before creating new code
- Use established project conventions
- Minimize dependencies and complexity
- Verify changes don't break existing functionality
- Use command chains to reduce tool calls (e.g., `git status && git diff`)

## Tool Usage

- Prefer `gh` CLI for GitHub operations
- Use `npx` for Node.js tools to avoid global installs
- Always use `git --no-pager` to avoid hangs
- Chain bash commands with `&&` for efficiency

## Security

- Never commit secrets or credentials
- Validate user input before shell operations
- Use `shlex.split()` for Python shell commands
- Avoid `eval` with user input

## MCP Servers Available

- **sequentialthinking**: Step-by-step reasoning for complex problems
- **playwright**: Browser automation and testing
- **octocode**: Package information lookup
- **markitdown**: Document conversion
- **github**: GitHub API operations (built-in)

## Response Style

- Be concise (3 sentences max for explanations)
- Make tool calls without excessive explanation
- Provide direct answers with examples when helpful
- Use parallel subagents for independent operations

---

*Last updated: 2025-12-13*
