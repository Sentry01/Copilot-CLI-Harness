#!/usr/bin/env bash
# Audit the app's locked runtime dependencies (run by CI: acceptance.yml / dependency-audit).
# Every supported layout is either audited or fails with instructions; none passes unchecked.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
APP=app
status=0
audited=0

fail() { echo "::error::$1"; status=1; }
# audit DIR CMD...: run CMD in DIR; any failure fails the audit.
audit() {
  local dir=$1 rc=0
  shift
  echo "\$ (cd $dir && $*)"
  (cd "$dir" && "$@") || rc=$?
  audited=1
  [ "$rc" -eq 0 ] || status=1
  return "$rc"
}

# Node.js
if [ -f "$APP/package-lock.json" ]; then
  audit "$APP" npm audit --audit-level=high --omit=dev
elif [ -f "$APP/pnpm-lock.yaml" ]; then
  audit "$APP" npx --yes pnpm@10 audit --prod --audit-level high
elif [ -f "$APP/yarn.lock" ]; then
  fail "$APP/yarn.lock: Yarn projects are not audited here. Use npm ($APP/package-lock.json) or pnpm, or add a Yarn audit step to this script."
elif [ -f "$APP/package.json" ]; then
  fail "$APP/package.json has no lockfile. Commit $APP/package-lock.json so dependencies are pinned and audited."
fi

# Python
if [ -f "$APP/requirements.txt" ]; then
  audit . pipx run pip-audit -r "$APP/requirements.txt"
elif [ -f "$APP/uv.lock" ]; then
  req="$(mktemp)"
  if audit . pipx run uv export --project "$APP" --frozen --no-dev --no-emit-project --format requirements-txt -o "$req"; then
    audit . pipx run pip-audit -r "$req" --disable-pip --no-deps
  fi
elif [ -f "$APP/poetry.lock" ]; then
  fail "$APP/poetry.lock is not audited directly. Export it (poetry export -f requirements.txt -o $APP/requirements.txt) and commit the file, or switch to uv."
elif [ -f "$APP/pyproject.toml" ] || [ -f "$APP/setup.py" ]; then
  fail "Python dependencies are not pinned. Commit $APP/requirements.txt (pinned) or $APP/uv.lock so they can be audited."
fi

# Go
if [ -f "$APP/go.mod" ]; then
  if command -v go >/dev/null; then
    audit "$APP" go run golang.org/x/vuln/cmd/govulncheck@latest ./...
  else
    fail "$APP/go.mod: Go is not installed on this runner; add actions/setup-go so govulncheck can run."
  fi
fi

[ "$audited" -eq 1 ] || [ "$status" -ne 0 ] || echo "No dependency manifests in $APP/; nothing to audit."
exit "$status"
