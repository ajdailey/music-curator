#!/usr/bin/env bash
# Run the test suite.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_venv
cd "${PROJECT_ROOT}"
exec "${VENV_DIR}/bin/python" -m pytest "$@"
