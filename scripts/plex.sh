#!/usr/bin/env bash
# Plex synchronization. Requires MUSIC_CURATOR_PLEX_URL and
# MUSIC_CURATOR_PLEX_TOKEN in the environment or a gitignored .env file.
#
#   ./scripts/plex.sh status
#   ./scripts/plex.sh index --refresh
#   ./scripts/plex.sh popularity        # export listener data for generation
#   ./scripts/plex.sh resolve
#   ./scripts/plex.sh diff
#   ./scripts/plex.sh sync              # dry run
#   ./scripts/plex.sh sync --apply      # writes
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ensure_venv
# plexapi is an optional extra; install on first use.
if ! "${VENV_DIR}/bin/python" -c "import plexapi" 2>/dev/null; then
    echo "Installing python-plexapi"
    "${VENV_DIR}/bin/python" -m pip install --quiet -e "${PROJECT_ROOT}[plex]"
fi
exec "${VENV_DIR}/bin/music-playlists" plex "$@" --project "${PROJECT_ROOT}"
