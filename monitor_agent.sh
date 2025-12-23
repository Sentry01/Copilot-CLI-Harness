#!/bin/bash
# Agent Monitor Script
# ====================
# Monitors the autonomous agent's progress in real-time.
# 
# Usage:
#   ./monitor_agent.sh                    # Monitor most recent log in harness_logs/
#   ./monitor_agent.sh /path/to/log       # Monitor specific log
#   ./monitor_agent.sh --project myapp    # Monitor specific project's latest log

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Default log location - harness_logs in the same directory as this script
HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
HARNESS_LOGS_DIR="${HARNESS_DIR}/harness_logs"
LOG_FILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --project|-p)
            PROJECT_NAME="$2"
            # Look for logs matching project name in harness_logs
            if [[ -d "$HARNESS_LOGS_DIR" ]]; then
                LOG_FILE=$(ls -t "$HARNESS_LOGS_DIR"/${PROJECT_NAME}_*.log 2>/dev/null | head -1)
            fi
            if [[ -z "$LOG_FILE" ]]; then
                echo -e "${RED}Error: No logs found for project: $PROJECT_NAME${NC}"
                echo "Looking in: $HARNESS_LOGS_DIR/${PROJECT_NAME}_*.log"
                exit 1
            fi
            shift 2
            ;;
        --list|-l)
            echo -e "${BOLD}${CYAN}Available log files:${NC}"
            if [[ -d "$HARNESS_LOGS_DIR" ]]; then
                ls -lht "$HARNESS_LOGS_DIR"/*.log 2>/dev/null | head -10 || echo "No logs found"
            else
                echo "No harness_logs directory found"
            fi
            exit 0
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS] [LOG_FILE]"
            echo ""
            echo "Options:"
            echo "  --project, -p NAME   Monitor logs for specified project"
            echo "  --list, -l           List recent log files"
            echo "  --help, -h           Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                           # Monitor most recent log"
            echo "  $0 --project showcase_test   # Monitor showcase_test project"
            echo "  $0 --list                    # List available logs"
            echo "  $0 harness_logs/myapp_*.log  # Monitor specific log"
            echo ""
            echo "Log location: $HARNESS_LOGS_DIR/"
            exit 0
            ;;
        *)
            LOG_FILE="$1"
            shift
            ;;
    esac
done

# If no log file specified, find the most recent one
if [[ -z "$LOG_FILE" ]]; then
    if [[ -d "$HARNESS_LOGS_DIR" ]]; then
        LOG_FILE=$(ls -t "$HARNESS_LOGS_DIR"/*.log 2>/dev/null | head -1)
    fi
fi

# Check if log file exists
if [[ -z "$LOG_FILE" ]] || [[ ! -f "$LOG_FILE" ]]; then
    echo -e "${RED}Error: No log file found${NC}"
    echo ""
    echo "Log directory: $HARNESS_LOGS_DIR/"
    echo ""
    echo "To start the agent with logging:"
    echo "  cd $HARNESS_DIR"
    echo "  python autonomous_agent_demo.py --project-dir PROJECT_NAME"
    echo ""
    echo "Logs are automatically saved to harness_logs/<project>_<timestamp>.log"
    exit 1
fi

# Clear screen and show header
clear
echo -e "${BOLD}${CYAN}╔════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║              AUTONOMOUS AGENT MONITOR                               ║${NC}"
echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Log file:${NC} $LOG_FILE"
echo -e "${BLUE}Started:${NC} $(date)"
echo -e "${YELLOW}Press Ctrl+C to stop monitoring${NC}"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════${NC}"
echo ""

# Function to colorize and format output
format_output() {
    while IFS= read -r line; do
        # Highlight different types of output
        if [[ "$line" =~ "✓" ]]; then
            echo -e "${GREEN}$line${NC}"
        elif [[ "$line" =~ "ERROR" ]] || [[ "$line" =~ "❌" ]]; then
            echo -e "${RED}$line${NC}"
        elif [[ "$line" =~ "📍 STATUS:" ]]; then
            echo -e "${CYAN}$line${NC}"
        elif [[ "$line" =~ "🔄 RETRY" ]]; then
            echo -e "${YELLOW}$line${NC}"
        elif [[ "$line" =~ "STAGE" ]]; then
            echo -e "${BOLD}${BLUE}$line${NC}"
        elif [[ "$line" =~ "SESSION" ]]; then
            echo -e "${BOLD}${CYAN}$line${NC}"
        elif [[ "$line" =~ "Progress:" ]] || [[ "$line" =~ "📊" ]]; then
            echo -e "${BOLD}${GREEN}$line${NC}"
        elif [[ "$line" =~ "Create" ]] || [[ "$line" =~ "Edit" ]]; then
            echo -e "${YELLOW}$line${NC}"
        else
            echo "$line"
        fi
    done
}

# Tail the log file with colorized output
tail -f "$LOG_FILE" 2>/dev/null | format_output
