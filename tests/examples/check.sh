#!/usr/bin/env bash
set -euo pipefail
fixture_dir="$(cd "$(dirname "$0")" && pwd)"
repo_dir="$(cd "$fixture_dir/../.." && pwd)"
example_dir="$(mktemp -d "${TMPDIR:-/tmp}/sb-skill-examples.XXXXXX")"
trap 'rm -rf "$example_dir"' EXIT
cp "$fixture_dir/pom.xml" "$example_dir/pom.xml"
python3 "$fixture_dir/extract.py" "$repo_dir" "$example_dir"
mvn -B -ntp -f "$example_dir/pom.xml" "-Dmaven.repo.local=${EXAMPLES_MAVEN_CACHE:-/tmp/sb-skill-examples-m2}" test "$@"

python3 "$fixture_dir/lifecycle.py" "$example_dir" "${EXAMPLES_MAVEN_CACHE:-/tmp/sb-skill-examples-m2}"
