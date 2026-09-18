#!/usr/bin/env bash
# Build the full audit report (zero counts, overlap, domination, coverage).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_export
require_sync
run_cli report
