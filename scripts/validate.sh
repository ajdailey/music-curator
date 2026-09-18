#!/usr/bin/env bash
# Structural validation of the knowledge files. Does not need the catalog.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
run_cli validate
