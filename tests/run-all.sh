#!/usr/bin/env bash
# Runs every suite under tests/<hook>/test-basic.sh and aggregates results.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

declare -a suites=()
pass=0; fail=0
for suite in */test-basic.sh; do
  suites+=("$suite")
done

printf '%-40s %-8s\n' "SUITE" "RESULT"
printf '%s\n' "---------------------------------------------------------------"
for suite in "${suites[@]}"; do
  out=$(bash "$suite" 2>&1)
  code=$?
  if [ "$code" -eq 0 ]; then
    printf '%-40s %-8s\n' "$suite" "PASS"
    pass=$((pass + 1))
  else
    printf '%-40s %-8s\n' "$suite" "FAIL"
    echo "$out" | sed 's/^/    /'
    fail=$((fail + 1))
  fi
done
echo
echo "Summary: $pass suite(s) passed, $fail suite(s) failed"
[ "$fail" -eq 0 ] || exit 1
