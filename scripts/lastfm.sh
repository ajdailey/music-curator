#!/usr/bin/env bash
# Optional Last.fm enrichment. Off by default and never part of any other flow.
# Requires MUSIC_CURATOR_LASTFM_API_KEY. Free key:
#   https://www.last.fm/api/account/create
#
#   ./scripts/lastfm.sh enrich --dry-run    report the gap, no requests
#   ./scripts/lastfm.sh enrich --limit 200  fill a bounded number
#   ./scripts/lastfm.sh enrich              fill the whole gap
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_venv
exec "${VENV_DIR}/bin/music-playlists" lastfm "$@" --project "${PROJECT_ROOT}"
