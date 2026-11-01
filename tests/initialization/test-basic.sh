#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 -m unittest discover -s "$ROOT/tests/initialization" -p 'test_*.py'
