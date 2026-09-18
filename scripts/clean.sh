#!/usr/bin/env bash
# Remove generated artifacts. Knowledge files and source exports are never
# touched: only things this project produces.
#
#   ./scripts/clean.sh              output, manifests, reports
#   ./scripts/clean.sh --cache      also the Plex index and Last.fm cache
#   ./scripts/clean.sh --all        also the popularity knowledge file
#   ./scripts/clean.sh --dry-run    list what would be removed
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CLEAN_CACHE=0
CLEAN_ALL=0
DRY=0
for arg in "$@"; do
    case "$arg" in
        --cache)   CLEAN_CACHE=1 ;;
        --all)     CLEAN_CACHE=1; CLEAN_ALL=1 ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

TARGETS=(
    "output/plex"
    "output/rockbox"
    "output/manifests"
    "reports/generation-summary.json"
    "reports/audit-report.json"
    "reports/plex-resolution.json"
    "reports/plex-diff.json"
    "reports/plex-sync.json"
)
[ "${CLEAN_CACHE}" -eq 1 ] && TARGETS+=(".cache")
# The popularity file costs a full Plex index to rebuild, so it survives
# an ordinary clean.
[ "${CLEAN_ALL}" -eq 1 ] && TARGETS+=("knowledge/song-popularity.json")

removed=0
for target in "${TARGETS[@]}"; do
    full="${PROJECT_ROOT}/${target}"
    [ -e "${full}" ] || continue
    if [ -d "${full}" ]; then
        count=$(find "${full}" -type f ! -name '.gitkeep' | wc -l)
        [ "${count}" -eq 0 ] && continue
        echo "  ${target}/ (${count} files)"
        if [ "${DRY}" -eq 0 ]; then
            find "${full}" -type f ! -name '.gitkeep' -delete
        fi
    else
        echo "  ${target}"
        [ "${DRY}" -eq 0 ] && rm -f "${full}"
    fi
    removed=$((removed + 1))
done

if [ "${removed}" -eq 0 ]; then
    echo "Nothing to clean."
elif [ "${DRY}" -eq 1 ]; then
    echo
    echo "Dry run. Nothing was removed."
else
    echo
    echo "Cleaned ${removed} target(s). Source exports and knowledge files untouched."
fi
