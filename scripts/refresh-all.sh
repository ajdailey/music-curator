#!/usr/bin/env bash
# The full refresh: reindex Plex, update popularity, regenerate local
# playlists, then synchronize to Plex.
#
#   ./scripts/refresh-all.sh                  everything up to a Plex dry run
#   ./scripts/refresh-all.sh --apply          same, then write to Plex
#   ./scripts/refresh-all.sh --skip-index     reuse the cached Plex index
#   ./scripts/refresh-all.sh --playlist NAME  limit the Plex sync to one playlist
#
# Stages: index -> popularity -> generate -> report -> plex sync.
# Nothing is written to Plex unless --apply is passed. Local playlist files in
# output/ are always regenerated.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

APPLY=0
SKIP_INDEX=0
PLAYLIST=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)      APPLY=1; shift ;;
        --skip-index) SKIP_INDEX=1; shift ;;
        --playlist)   [ $# -ge 2 ] || { echo "--playlist needs a name" >&2; exit 1; }
                      PLAYLIST="$2"; shift 2 ;;
        --playlist=*) PLAYLIST="${1#*=}"; shift ;;
        -h|--help)    sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

ensure_venv
require_export
require_sync
if ! "${VENV_DIR}/bin/python" -c "import plexapi" 2>/dev/null; then
    echo "Installing python-plexapi"
    "${VENV_DIR}/bin/python" -m pip install --quiet -e "${PROJECT_ROOT}[plex]"
fi

CLI=("${VENV_DIR}/bin/music-playlists")
run_step() { "${CLI[@]}" "$@" --project "${PROJECT_ROOT}"; }

stage() {
    echo
    echo "=============================================================="
    echo "==> $1"
    echo "=============================================================="
}

# 1. Plex library index. Feeds both popularity and track resolution.
if [ "${SKIP_INDEX}" -eq 1 ]; then
    echo "Skipping index refresh; using the cached index."
else
    stage "plex index (refresh)"
    run_step plex index --refresh || exit $?
fi

# 2. Popularity knowledge, derived from the index. Offline, fast.
stage "plex popularity"
run_step plex popularity || exit $?

# 3. Regenerate local playlists, now using the refreshed popularity data.
stage "generate"
run_step generate || exit $?

# 4. Audit what changed.
stage "report"
run_step report || exit $?

# 5. Synchronize to Plex. Dry run unless --apply.
stage "plex sync"
SYNC_ARGS=()
[ -n "${PLAYLIST}" ] && SYNC_ARGS=(--playlist "${PLAYLIST}")
[ "${APPLY}" -eq 1 ] && SYNC_ARGS+=(--apply)
STATUS=0
"${PROJECT_ROOT}/scripts/plex-sync.sh" "${SYNC_ARGS[@]}" || STATUS=$?

echo
if [ "${STATUS}" -eq 0 ]; then
    if [ "${APPLY}" -eq 1 ]; then
        echo "Refresh complete. Plex playlists updated; local files in output/."
    else
        echo "Refresh complete. Local files in output/ updated."
        echo "Plex was a dry run; re-run with --apply to write."
    fi
else
    echo "Local files in output/ updated. The Plex sync did not complete." >&2
fi
exit "${STATUS}"
