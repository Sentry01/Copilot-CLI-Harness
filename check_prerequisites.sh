#!/bin/bash
# ============================================================================
# Copilot Harness V1 - Prerequisites Checker
# ============================================================================
# Run this script to verify all dependencies are installed before using
# the autonomous coding agent harness.
#
# Usage: ./check_prerequisites.sh
# ============================================================================

# Don't use set -e as arithmetic operations can return non-zero
# set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASS=0
FAIL=0
WARN=0

# Print functions
print_header() {
    echo ""
    echo -e "${BLUE}============================================================================${NC}"
    echo -e "${BLUE}  Copilot Harness - Prerequisites Check${NC}"
    echo -e "${BLUE}============================================================================${NC}"
    echo ""
}

print_check() {
    echo -e "${BLUE}Checking:${NC} $1"
}

print_pass() {
    echo -e "  ${GREEN}✅ PASS:${NC} $1"
    PASS=$((PASS + 1))
}

print_fail() {
    echo -e "  ${RED}❌ FAIL:${NC} $1"
    FAIL=$((FAIL + 1))
}

print_warn() {
    echo -e "  ${YELLOW}⚠️  WARN:${NC} $1"
    WARN=$((WARN + 1))
}

print_info() {
    echo -e "  ${BLUE}ℹ️  INFO:${NC} $1"
}

print_fix() {
    echo -e "  ${YELLOW}   Fix:${NC} $1"
}

# ============================================================================
# Prerequisite Checks
# ============================================================================

check_copilot_cli() {
    print_check "GitHub Copilot CLI"
    
    if command -v copilot &> /dev/null; then
        VERSION=$(copilot --version 2>&1 | head -1)
        print_pass "Copilot CLI installed (v$VERSION)"
        
        # Check minimum version (0.0.354+)
        PATCH_VERSION=$(echo "$VERSION" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | cut -d. -f3)
        if [ -n "$PATCH_VERSION" ] && [ "$PATCH_VERSION" -ge 354 ] 2>/dev/null; then
            print_pass "Version meets minimum requirement (0.0.354+)"
        else
            print_info "Could not verify version requirement (0.0.354+)"
        fi
    else
        print_fail "Copilot CLI not found"
        print_fix "npm install -g @githubnext/github-copilot-cli"
        print_info "See: https://docs.github.com/en/copilot/using-github-copilot/using-github-copilot-in-the-command-line"
    fi
    echo ""
}

check_copilot_auth() {
    print_check "Copilot CLI Authentication"
    
    if command -v copilot &> /dev/null; then
        # Try a simple prompt to check auth
        AUTH_RESULT=$(copilot -p "respond with exactly: ok" 2>&1 | head -5)
        
        if echo "$AUTH_RESULT" | grep -qi "ok\|authenticated\|claude\|gpt"; then
            print_pass "Copilot CLI is authenticated"
        elif echo "$AUTH_RESULT" | grep -qi "error\|unauthorized\|login\|auth"; then
            print_fail "Copilot CLI not authenticated"
            print_fix "copilot auth login"
        else
            print_warn "Could not verify authentication status"
            print_info "Try running: copilot -p 'hello'"
        fi
    else
        print_info "Skipped (Copilot CLI not installed)"
    fi
    echo ""
}

check_python() {
    print_check "Python 3"
    
    if command -v python3 &> /dev/null; then
        VERSION=$(python3 --version 2>&1)
        print_pass "$VERSION"
        
        # Check Python version >= 3.10
        PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
            print_pass "Python version meets requirement (3.10+)"
        else
            print_warn "Python 3.10+ recommended for best compatibility"
        fi
    else
        print_fail "Python 3 not found"
        print_fix "Install Python 3.10+ from https://python.org"
    fi
    echo ""
}

check_playwright_mcp() {
    print_check "Playwright MCP Server"
    
    # The Playwright MCP server runs via npx, not Python
    # Just verify npx can find the package
    if command -v npx &> /dev/null; then
        # Check if we can resolve the package (don't actually run it)
        if npm view @playwright/mcp version &> /dev/null; then
            VERSION=$(npm view @playwright/mcp version 2>/dev/null || echo "latest")
            print_pass "Playwright MCP package available (v$VERSION)"
            print_info "Runs via: npx @playwright/mcp@latest"
        else
            print_warn "Could not verify @playwright/mcp package"
            print_info "Will be auto-downloaded on first use via npx"
        fi
    else
        print_fail "npx not available (required for Playwright MCP)"
        print_fix "Install Node.js which includes npx"
    fi
    echo ""
}

check_playwright_browsers() {
    print_check "Playwright Browsers"
    
    BROWSER_PATH=""
    
    # Check common browser cache locations
    if [ -d "$HOME/Library/Caches/ms-playwright" ]; then
        BROWSER_PATH="$HOME/Library/Caches/ms-playwright"
    elif [ -d "$HOME/.cache/ms-playwright" ]; then
        BROWSER_PATH="$HOME/.cache/ms-playwright"
    fi
    
    if [ -n "$BROWSER_PATH" ]; then
        CHROMIUM=$(ls "$BROWSER_PATH" 2>/dev/null | grep -c "chromium" || echo "0")
        FIREFOX=$(ls "$BROWSER_PATH" 2>/dev/null | grep -c "firefox" || echo "0")
        WEBKIT=$(ls "$BROWSER_PATH" 2>/dev/null | grep -c "webkit" || echo "0")
        
        if [ "$CHROMIUM" -gt 0 ]; then
            print_pass "Chromium browser installed"
        else
            print_warn "Chromium browser not found"
            print_fix "npx playwright install chromium"
        fi
        
        if [ "$FIREFOX" -gt 0 ]; then
            print_pass "Firefox browser installed"
        fi
        
        if [ "$WEBKIT" -gt 0 ]; then
            print_pass "WebKit browser installed"
        fi
    else
        print_warn "Playwright browser cache not found"
        print_info "Browsers will be auto-downloaded on first MCP use"
        print_fix "Or pre-install with: npx playwright install chromium"
    fi
    echo ""
}

check_nodejs() {
    print_check "Node.js"
    
    if command -v node &> /dev/null; then
        VERSION=$(node --version 2>&1)
        print_pass "Node.js installed ($VERSION)"
        
        # Check version >= 18
        NODE_MAJOR=$(echo "$VERSION" | sed 's/v//' | cut -d. -f1)
        if [ "$NODE_MAJOR" -ge 18 ]; then
            print_pass "Node.js version meets requirement (18+)"
        else
            print_warn "Node.js 18+ recommended"
            print_fix "Install newer Node.js from https://nodejs.org"
        fi
    else
        print_fail "Node.js not found"
        print_fix "Install Node.js 18+ from https://nodejs.org"
    fi
    echo ""
}

check_npm_npx() {
    print_check "npm and npx (for MCP servers)"
    
    if command -v npm &> /dev/null; then
        NPM_VERSION=$(npm --version 2>&1)
        print_pass "npm installed (v$NPM_VERSION)"
    else
        print_fail "npm not found"
        print_fix "Install Node.js which includes npm"
    fi
    
    if command -v npx &> /dev/null; then
        NPX_VERSION=$(npx --version 2>&1)
        print_pass "npx installed (v$NPX_VERSION)"
    else
        print_fail "npx not found"
        print_fix "Install Node.js which includes npx"
    fi
    echo ""
}

check_mcp_config() {
    print_check "MCP Configuration (MCP.json)"
    
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    MCP_FILE="$SCRIPT_DIR/MCP.json"
    
    if [ -f "$MCP_FILE" ]; then
        print_pass "MCP.json found"
        
        # Check if it contains playwright config
        if grep -q "playwright" "$MCP_FILE"; then
            print_pass "Playwright MCP server configured"
        else
            print_warn "Playwright MCP server not found in config"
        fi
    else
        print_fail "MCP.json not found"
        print_fix "Create MCP.json with Playwright MCP configuration"
    fi
    echo ""
}

check_project_files() {
    print_check "Project Files"
    
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    FILES=(
        "autonomous_agent_demo.py"
        "agent.py"
        "copilot_client.py"
        "security.py"
        "prompts/coding_prompt.md"
        "prompts/initializer_prompt.md"
        "prompts/app_spec.txt"
    )
    
    ALL_PRESENT=true
    for FILE in "${FILES[@]}"; do
        if [ -f "$SCRIPT_DIR/$FILE" ]; then
            print_pass "$FILE exists"
        else
            print_fail "$FILE missing"
            ALL_PRESENT=false
        fi
    done
    echo ""
}

# ============================================================================
# Summary
# ============================================================================

print_summary() {
    echo -e "${BLUE}============================================================================${NC}"
    echo -e "${BLUE}  Summary${NC}"
    echo -e "${BLUE}============================================================================${NC}"
    echo ""
    echo -e "  ${GREEN}Passed:${NC}  $PASS"
    echo -e "  ${RED}Failed:${NC}  $FAIL"
    echo -e "  ${YELLOW}Warnings:${NC} $WARN"
    echo ""
    
    if [ "$FAIL" -eq 0 ]; then
        echo -e "${GREEN}🎉 All prerequisites met! You're ready to run the harness.${NC}"
        echo ""
        echo "  Quick start:"
        echo "    python autonomous_agent_demo.py --project-dir ./my_project"
        echo ""
    else
        echo -e "${RED}⚠️  Some prerequisites are missing. Please fix the issues above.${NC}"
        echo ""
        echo "  Common fixes:"
        echo "    npx playwright install chromium  # Install browser"
        echo "    copilot auth login               # Authenticate Copilot CLI"
        echo ""
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    print_header
    
    check_copilot_cli
    check_copilot_auth
    check_python
    check_playwright_mcp
    check_playwright_browsers
    check_nodejs
    check_npm_npx
    check_mcp_config
    check_project_files
    
    print_summary
    
    # Exit with error code if any failures
    if [ "$FAIL" -gt 0 ]; then
        exit 1
    fi
}

main "$@"
