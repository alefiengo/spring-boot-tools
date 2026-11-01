#!/usr/bin/env bash
# Explicit opt-in integration: installed tools only; dependencies may use registries.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec node "$ROOT/tests/build-fixtures/check.cjs" "$@"
