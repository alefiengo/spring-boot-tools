#!/usr/bin/env bash
# Runs the deterministic local checks from CONTRIBUTING.md and prints an
# environment header so the output can be pasted into a pull request.
#
# Usage: scripts/validate-local.sh [--with-npm-ci] [--help]
#
# No network access unless --with-npm-ci is given (npm ci downloads packages).
# Behavioral evaluations, Java examples and real-build fixtures make model
# calls or download dependencies and are not run here.
set -euo pipefail

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

with_npm_ci=0
for arg in "$@"; do
  case "$arg" in
    --with-npm-ci) with_npm_ci=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

version_of() {
  # Print the first line of "<command> <flag>" or "not found".
  local command_name="$1"; shift
  if command -v "$command_name" >/dev/null 2>&1; then
    "$command_name" "$@" 2>&1 | head -n 1 || true
  else
    printf 'not found'
  fi
}

gradle_version="not found"
if command -v gradle >/dev/null 2>&1; then
  gradle_version="$(gradle --version 2>&1 | grep -m1 '^Gradle ' || printf 'unknown')"
fi

kernel="$(uname -sr)"
wsl="no"
if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
  wsl="yes (${WSL_DISTRO_NAME:-unnamed distribution})"
fi
commit="$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'unknown')"
dirty="clean"
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  dirty="uncommitted changes to tracked files"
fi

printf '== Environment\n'
printf 'OS/kernel : %s\n' "$kernel"
printf 'WSL       : %s\n' "$wsl"
printf 'Node      : %s\n' "$(version_of node --version)"
printf 'npm       : %s\n' "$(version_of npm --version)"
printf 'Python    : %s\n' "$(version_of python3 --version)"
printf 'Java      : %s\n' "$(version_of java -version)"
printf 'Maven     : %s\n' "$(version_of mvn --version)"
printf 'Gradle    : %s\n' "$gradle_version"
printf 'Claude    : %s\n' "$(version_of claude --version)"
printf 'Git commit: %s (%s, %s)\n' "$commit" "$branch" "$dirty"
printf 'Date (UTC): %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

passed=0
failed=0
skipped=0
failures=()

run_check() {
  local label="$1"; shift
  printf '== %s\n' "$label"
  if "$@"; then
    printf -- '-- PASS: %s\n\n' "$label"
    passed=$((passed + 1))
  else
    printf -- '-- FAIL: %s\n\n' "$label"
    failed=$((failed + 1))
    failures+=("$label")
  fi
}

skip_check() {
  printf '== %s\n-- SKIPPED: %s\n\n' "$1" "$2"
  skipped=$((skipped + 1))
}

if [ "$with_npm_ci" -eq 1 ]; then
  run_check "npm ci" npm ci
else
  printf '== npm ci\n-- not requested (pass --with-npm-ci); node_modules must already be installed\n\n'
fi

run_check "node --check mcp-server/index.js" node --check mcp-server/index.js

run_check "python3 -m py_compile (runtime, hooks, doctor)" \
  python3 -m py_compile \
    runtime/project_runtime.py runtime/project_initializer.py runtime/sql_lint.py \
    hooks/scripts/hook-runtime.py scripts/install-doctor.py

run_check "bash tests/run-all.sh" bash tests/run-all.sh

if command -v claude >/dev/null 2>&1; then
  run_check "claude plugin validate .claude-plugin/plugin.json" \
    claude plugin validate .claude-plugin/plugin.json --strict
  run_check "claude plugin validate .claude-plugin/marketplace.json" \
    claude plugin validate .claude-plugin/marketplace.json --strict
  run_check "claude plugin validate skills" claude plugin validate skills --strict
  run_check "claude plugin validate agents" claude plugin validate agents --strict
  run_check "claude plugin validate docs/skills" claude plugin validate docs/skills --strict
else
  skip_check "claude plugin validate (manifests, skills, agents, docs/skills)" \
    "the claude CLI is not on PATH"
fi

run_check "git diff --check (working tree)" git diff --check
run_check "git diff --cached --check (index)" git diff --cached --check

printf '== Summary\n'
printf '%d passed, %d failed, %d skipped\n' "$passed" "$failed" "$skipped"
if [ "$failed" -gt 0 ]; then
  printf 'Failed checks:\n'
  printf '  - %s\n' "${failures[@]}"
  exit 1
fi
if [ "$skipped" -gt 0 ]; then
  printf 'Skipped checks were not exercised; report them as not run.\n'
fi
