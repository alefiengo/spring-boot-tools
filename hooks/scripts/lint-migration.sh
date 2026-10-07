#!/usr/bin/env bash
# Requires Python 3; missing runtime fails open with an explicit diagnostic.
set -uo pipefail
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '{"systemMessage":"Spring Boot hook skipped: Python 3 is required."}'
  exit 0
fi
exec python3 "$(dirname "${BASH_SOURCE[0]}")/hook-runtime.py" lint-migration
