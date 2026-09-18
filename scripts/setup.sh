#!/usr/bin/env bash
# One-time setup: install the system prerequisites, create the virtualenv, and
# install the package. Safe to re-run.
#
#   ./scripts/setup.sh          prompt before installing system packages
#   ./scripts/setup.sh --yes    install system packages without prompting
#   ./scripts/setup.sh --check  report what is missing and change nothing

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MIN_VERSION="3.11"

ASSUME_YES=0
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes)   ASSUME_YES=1 ;;
        --check)    CHECK_ONLY=1 ;;
        -h|--help)  sed -n '2,8p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)          echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }

# Print the install command for whichever package manager this host uses.
# Distributions split the standard library differently: Debian and Ubuntu ship
# venv and pip as separate packages, while the RPM and Arch families include
# venv with the interpreter.
system_install_command() {
    if command -v apt-get >/dev/null 2>&1; then
        echo "sudo apt-get install -y python3-venv python3-pip"
    elif command -v dnf >/dev/null 2>&1; then
        echo "sudo dnf install -y python3-pip"
    elif command -v yum >/dev/null 2>&1; then
        echo "sudo yum install -y python3-pip"
    elif command -v zypper >/dev/null 2>&1; then
        echo "sudo zypper install -y python3-pip"
    elif command -v pacman >/dev/null 2>&1; then
        echo "sudo pacman -S --noconfirm python-pip"
    fi
}

echo "==> Checking Python"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 \
    || fail "${PYTHON_BIN} not found. Install Python ${MIN_VERSION} or newer."

"${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail "$("${PYTHON_BIN}" -V) is too old. Python ${MIN_VERSION} or newer is required."
echo "    $("${PYTHON_BIN}" -V) at $(command -v "${PYTHON_BIN}")"

echo "==> Checking the venv module"
# python3 -m venv exists but fails at the bootstrap step when ensurepip is
# absent, which is the usual Debian and Ubuntu default. Test for both.
venv_ok=1
"${PYTHON_BIN}" -c 'import venv' >/dev/null 2>&1 || venv_ok=0
"${PYTHON_BIN}" -c 'import ensurepip' >/dev/null 2>&1 || venv_ok=0

if [ "${venv_ok}" -eq 0 ]; then
    install_cmd="$(system_install_command)"
    echo "    missing: the venv module cannot bootstrap pip"
    if [ "${CHECK_ONLY}" -eq 1 ]; then
        [ -n "${install_cmd}" ] && echo "    fix with: ${install_cmd}"
    elif [ -z "${install_cmd}" ]; then
        fail "No supported package manager found. Install the Python venv and pip
       packages for your distribution, then re-run this script."
    else
        echo "    fix with: ${install_cmd}"
        if [ "${ASSUME_YES}" -ne 1 ]; then
            read -r -p "    Run it now? [y/N] " reply
            case "${reply}" in
                [yY]|[yY][eE][sS]) ;;
                *) fail "Declined. Run the command above, then re-run this script." ;;
            esac
        fi
        eval "${install_cmd}"
        "${PYTHON_BIN}" -c 'import venv, ensurepip' >/dev/null 2>&1 \
            || fail "venv is still unavailable after installing. Check the output above."
    fi
else
    echo "    ok"
fi

if [ "${CHECK_ONLY}" -eq 1 ]; then
    echo "==> Checking the virtualenv"
    if [ -x "${VENV_DIR}/bin/music-playlists" ]; then
        echo "    ok: ${VENV_DIR}"
    else
        echo "    missing: run ./scripts/setup.sh to create it"
    fi
    exit 0
fi

echo "==> Creating the virtualenv"
if [ -x "${VENV_DIR}/bin/python" ]; then
    echo "    reusing ${VENV_DIR}"
else
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    echo "    created ${VENV_DIR}"
fi

echo "==> Installing music-curator (editable, with plex and dev extras)"
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip
"${VENV_DIR}/bin/python" -m pip install --quiet -e "${PROJECT_ROOT}[plex,dev]"
touch "${VENV_DIR}/.install-stamp"

echo "==> Verifying"
"${VENV_DIR}/bin/music-playlists" --help >/dev/null
"${VENV_DIR}/bin/python" -m pytest --version >/dev/null
echo "    music-playlists and pytest are runnable"

cat <<EOF

Setup complete.

Next:
  ./scripts/validate.sh    knowledge-file checks, no catalog needed
  ./scripts/test.sh        test suite
  ./scripts/check.sh       validate, test, generate, report

Generation needs two exports in the project root; see README.md:
  Library Playlist.m3u8    the catalog
  iPod Sync.m3u8           the portable library

For Plex synchronization, copy .env.example to .env and fill in the token.
EOF
