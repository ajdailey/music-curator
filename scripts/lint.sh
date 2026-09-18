#!/usr/bin/env bash
# Ruff over the whole tree. The rule selection is in pyproject.toml.
#
#   ./scripts/lint.sh          report findings
#   ./scripts/lint.sh --fix    apply the fixable ones
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_venv

exec "${VENV_DIR}/bin/python" -m ruff check "${PROJECT_ROOT}" "$@"
