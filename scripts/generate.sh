#!/usr/bin/env bash
# Generate all playlists for every configured target.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_export
require_sync
run_cli generate
