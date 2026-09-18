#!/usr/bin/env bash
# Full pipeline: validate, lint, test, generate, report. Use this for a normal
# run.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_export
require_sync

echo "==> validate"
run_cli validate

echo "==> ruff"
"${VENV_DIR}/bin/python" -m ruff check "${PROJECT_ROOT}"

echo "==> pytest"
(cd "${PROJECT_ROOT}" && "${VENV_DIR}/bin/python" -m pytest -q)

echo "==> generate"
run_cli generate

echo "==> report"
run_cli report
