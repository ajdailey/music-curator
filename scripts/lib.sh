#!/usr/bin/env bash
# Shared bootstrap for the music-curator scripts.
# Resolves the project root, creates the virtualenv on first use, and installs
# the package in editable mode when it is missing or out of date.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Overridable so a containerized run keeps its virtualenv outside the tree.
# The host's .venv holds the host interpreter's absolute paths; a container
# writing to it would leave the host scripts pointing at a Python that is not
# there. compose.yaml sets this to a named volume.
VENV_DIR="${MUSIC_CURATOR_VENV:-${PROJECT_ROOT}/.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

ensure_venv() {
    if [ ! -e "${VENV_DIR}/bin/python" ] && [ ! -L "${VENV_DIR}/bin/python" ]; then
        echo "Creating virtualenv at ${VENV_DIR}"
        "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    elif ! "${VENV_DIR}/bin/python" -c "" >/dev/null 2>&1; then
        # The interpreter the venv links to is gone, as after a base-image or
        # host Python upgrade. --clear empties the directory without removing
        # it, which a volume mount point requires; the stamp goes with it.
        echo "Rebuilding virtualenv at ${VENV_DIR}: its interpreter does not run"
        "${PYTHON_BIN}" -m venv --clear "${VENV_DIR}"
    fi

    # Reinstall when the entry point is missing or pyproject is newer than the
    # installed metadata, so dependency changes are picked up automatically.
    local marker="${VENV_DIR}/.install-stamp"
    if [ ! -x "${VENV_DIR}/bin/music-playlists" ] \
       || [ ! -f "${marker}" ] \
       || [ "${PROJECT_ROOT}/pyproject.toml" -nt "${marker}" ]; then
        # The same extras setup.sh and setup.bat install. Without plex the
        # Plex scripts and tests/test_plex.py fail on an import, and this
        # bootstrap is the only install a host that skipped setup ever gets.
        echo "Installing music-curator (editable, with plex and dev extras)"
        "${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip
        "${VENV_DIR}/bin/python" -m pip install --quiet -e "${PROJECT_ROOT}[plex,dev]"
        touch "${marker}"
    fi
}

run_cli() {
    ensure_venv
    "${VENV_DIR}/bin/music-playlists" "$@" --project "${PROJECT_ROOT}"
}

# True when DIR is the project root or inside output/ or reports/, where
# publish.sh would delete its sources before copying them. Compared by inode,
# so the test also holds for /out in the container, a second mount of the tree.
publish_dest_refused() {
    local d
    d="$(realpath -m "$1")"
    if [ "${d}/output" -ef "${PROJECT_ROOT}/output" ] \
       || [ "${d}/reports" -ef "${PROJECT_ROOT}/reports" ]; then
        return 0
    fi
    while :; do
        if [ "$d" -ef "${PROJECT_ROOT}/output" ] || [ "$d" -ef "${PROJECT_ROOT}/reports" ]; then
            return 0
        fi
        [ "$d" != / ] || return 1
        d="$(dirname "$d")"
    done
}

require_export() {
    # The library export is the catalog; without it nothing can generate.
    if [ ! -e "${PROJECT_ROOT}/Library Playlist.m3u8" ]; then
        echo "ERROR: 'Library Playlist.m3u8' is missing from ${PROJECT_ROOT}" >&2
        echo "       Export the full library from your music software, or symlink it:" >&2
        echo "       ln -s /path/to/'Library Playlist.m3u8' '${PROJECT_ROOT}/'" >&2
        return 1
    fi
}

require_sync() {
    if [ ! -e "${PROJECT_ROOT}/iPod Sync.m3u8" ]; then
        echo "ERROR: 'iPod Sync.m3u8' is missing from ${PROJECT_ROOT}" >&2
        echo "       Rockbox output is the intersection with this playlist." >&2
        return 1
    fi
}
