#!/usr/bin/env bash
# Run the whole test suite and summarise it.
#
# Each test file is a standalone script that exits non-zero on failure -- there is no
# pytest here, deliberately: the suite has to run from a bare checkout with nothing but
# the project's own dependencies installed. This wrapper exists so the same command works
# on a laptop, in the Windows VM and in CI, instead of three copies of a for-loop that
# drift apart.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

pass=0; fail=0; failed=()
for t in tests/test_*.py; do
    if out=$("$PY" "$t" 2>&1); then
        pass=$((pass + 1))
    else
        fail=$((fail + 1)); failed+=("$(basename "$t")")
        echo "--- FAIL: $(basename "$t") ---"
        echo "$out" | tail -12
    fi
done

echo
echo "=== $pass passed, $fail failed ==="
[ "$fail" -eq 0 ] || { echo "failed: ${failed[*]}"; exit 1; }
