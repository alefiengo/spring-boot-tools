#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
python3 -B -m unittest discover -s "$ROOT/tests/install-doctor" -v
