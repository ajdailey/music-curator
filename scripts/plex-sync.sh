#!/usr/bin/env bash
# High-level Plex synchronization: the whole flow in the safe order.
#
#   ./scripts/plex-sync.sh                          full dry run, changes nothing
#   ./scripts/plex-sync.sh --apply                  dry run, then write
#   ./scripts/plex-sync.sh --playlist "Grunge"      limit to one playlist
#   ./scripts/plex-sync.sh --refresh-index          rebuild the library index first
#   ./scripts/plex-sync.sh --generate               regenerate playlists first
#
# Stages: status -> index -> resolve -> diff -> sync.
# Every stage before sync is read-only. Nothing is written to Plex unless
# --apply is passed, and each stage must succeed before the next one runs.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

APPLY=0
REFRESH_INDEX=0
REGENERATE=0
PLAYLIST=""
PASSTHROUGH=()

while [ $# -gt 0 ]; do
    case "$1" in
        --apply)          APPLY=1; shift ;;
        --refresh-index)  REFRESH_INDEX=1; shift ;;
        --generate)       REGENERATE=1; shift ;;
        --playlist)       [ $# -ge 2 ] || { echo "--playlist needs a name" >&2; exit 1; }
                          PLAYLIST="$2"; shift 2 ;;
        --playlist=*)     PLAYLIST="${1#*=}"; shift ;;
        -h|--help)        sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)                PASSTHROUGH+=("$1"); shift ;;
    esac
done

ensure_venv
if ! "${VENV_DIR}/bin/python" -c "import plexapi" 2>/dev/null; then
    echo "Installing python-plexapi"
    "${VENV_DIR}/bin/python" -m pip install --quiet -e "${PROJECT_ROOT}[plex]"
fi

CLI=("${VENV_DIR}/bin/music-playlists")
SCOPE=()
[ -n "${PLAYLIST}" ] && SCOPE=(--playlist "${PLAYLIST}")

stage() {
    echo
    echo "=============================================================="
    echo "==> $1"
    echo "=============================================================="
}

plex() {
    "${CLI[@]}" plex "$@" --project "${PROJECT_ROOT}"
}

# 1. Connection. Fails clean if .env or the environment is incomplete.
stage "status"
plex status || exit $?

# 2. Playlist manifests are the contract for synchronization.
if [ "${REGENERATE}" -eq 1 ]; then
    stage "generate"
    require_export
    require_sync
    "${CLI[@]}" generate --project "${PROJECT_ROOT}" >/dev/null || exit $?
    echo "Playlists and manifests regenerated."
fi

if [ ! -d "${PROJECT_ROOT}/output/manifests" ] \
   || [ -z "$(ls -A "${PROJECT_ROOT}/output/manifests" 2>/dev/null)" ]; then
    echo
    echo "ERROR: no playlist manifests in output/manifests." >&2
    echo "       Run './scripts/generate.sh' or re-run with --generate." >&2
    exit 1
fi

# 3. Library index. A full index of a large library takes a long time, so it
#    is checkpointed: an interrupted run resumes instead of restarting.
stage "index"
if [ "${REFRESH_INDEX}" -eq 1 ]; then
    plex index --refresh || exit $?
elif [ -f "${PROJECT_ROOT}/.cache/plex-index.json" ] \
     && grep -q '"complete": false' "${PROJECT_ROOT}/.cache/plex-index.json" 2>/dev/null; then
    echo "Partial index found; resuming."
    plex index --resume || exit $?
else
    plex index || exit $?
fi

# 4. Resolve manifests against the index. Read-only.
stage "resolve"
plex resolve "${SCOPE[@]}" || exit $?

# 5. Compare against what Plex currently holds. Read-only.
stage "diff"
plex diff "${SCOPE[@]}" || exit $?

# 6. The dry run always runs first. An apply is abandoned if it reports a
#    refusal, so a change is never written unreviewed.
stage "sync (dry run)"
DRY_STATUS=0
plex sync "${SCOPE[@]}" "${PASSTHROUGH[@]}" || DRY_STATUS=$?
if [ "${DRY_STATUS}" -ne 0 ]; then
    echo
    echo "Dry run reported refusals. Review reports/plex-sync.json." >&2
    [ "${APPLY}" -eq 1 ] && echo "Not applying." >&2
    exit "${DRY_STATUS}"
fi

if [ "${APPLY}" -ne 1 ]; then
    echo
    echo "Dry run complete. Nothing was written to Plex."
    echo "Re-run with --apply to write these changes."
    exit 0
fi

stage "sync (apply)"
APPLY_STATUS=0
plex sync "${SCOPE[@]}" "${PASSTHROUGH[@]}" --apply || APPLY_STATUS=$?

echo
if [ "${APPLY_STATUS}" -eq 0 ]; then
    echo "Sync complete. Managed playlists are prefixed 'MC - '."
else
    echo "Sync finished with refusals. Review reports/plex-sync.json." >&2
fi
exit "${APPLY_STATUS}"
