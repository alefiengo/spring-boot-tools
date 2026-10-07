#!/usr/bin/env bash
set -euo pipefail
node --test "$(dirname "${BASH_SOURCE[0]}")/packaging.test.mjs"
