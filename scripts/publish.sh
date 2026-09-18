#!/usr/bin/env bash
# Copy the generated playlists, manifests and reports to another directory,
# without regenerating them. Runs on the host and in the container alike.
#
#   scripts/publish.sh /media/sdcard
#   scripts/publish.sh                  # MUSIC_CURATOR_OUT_DIR, else dist/
set -euo pipefail

# A given destination is relative to the caller; the dist/ default is relative
# to the project root.
DEST="${1:-${MUSIC_CURATOR_OUT_DIR:-}}"
case "$DEST" in
  "" | /*) ;;
  *) DEST="${PWD}/${DEST}" ;;
esac

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "${PROJECT_ROOT}"
DEST="$(realpath -m "${DEST:-${PWD}/dist}")"

if [ "$DEST" = / ]; then
  echo "ERROR: refusing to publish to '/'" >&2
  exit 1
fi
if publish_dest_refused "$DEST"; then
  echo "ERROR: refusing to publish to '${DEST}': it is the project root, or inside output/ or reports/" >&2
  exit 1
fi

if [ ! -d output ]; then
    echo "ERROR: no output/ directory; run scripts/generate.sh first" >&2
    exit 1
fi

mkdir -p "$DEST"

# Only the two directories this script owns are removed, so a destination
# holding anything else keeps it. Replacing rather than merging, so a playlist
# dropped from the taxonomy does not survive in the published copy.
rm -rf "${DEST}/output" "${DEST}/reports"
cp -R output reports "${DEST}/"

echo ">>> Published to ${DEST}"
ls -l "$DEST"
