#!/usr/bin/env bash
# Runs the containerized pipeline on Linux, WSL included. Windows goes through
# scripts/container.bat.
#
# compose.yaml holds the configuration - mounts, caches, environment. This adds
# only ergonomics: subcommand names, the publish directory, and running as the
# caller where that is the right answer. Anything here that outgrows that
# belongs in compose.yaml instead, so both platforms keep getting it.
set -euo pipefail

# Captured before the cd, so a relative --out or publish DIR names a directory
# relative to the caller.
CALLER_PWD="$PWD"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "${PROJECT_ROOT}"

SERVICE=curator
IMAGE="${MUSIC_CURATOR_IMAGE:-music-curator:local}"

say() { printf '==> %s\n' "$1"; }
die() { printf '%s\n' "$1" >&2; exit 1; }

usage() {
  cat <<'EOF'
usage: container.sh [--rebuild] [--out DIR] [command]

  (no command)   check: validate, ruff, pytest, generate, report
  validate       knowledge-file validation, no catalog needed
  lint           ruff; --fix applies the fixable findings
  test           the test suite
  generate       write playlists into output/
  report         write and print the audit report
  publish [DIR]  copy output/ and reports/ to DIR, else the --out directory
  clean          remove generated output and reports
  shell          an interactive shell in the image
  <anything>     run verbatim inside the image, at the project root

  --rebuild      rebuild the image first
  --out DIR      the publish destination on this host (default: dist/)

environment:
  MUSIC_CURATOR_IMAGE             image name (default music-curator:local)
  MUSIC_CURATOR_CONTAINER_ENGINE  docker or podman (default: whichever is present)
  MUSIC_CURATOR_OUT               default publish directory, as --out

Generated playlists land in output/ and reports/ in the working tree, which is
bind-mounted, so they are on this host whether or not publish is used.
EOF
}

# --- host --------------------------------------------------------------------

# Refused rather than half-handled: under MSYS a mount source has to be in
# Windows form, and the paths this builds are the /c/... ones the shell reports.
# container.bat is the Windows launcher and runs from any Windows shell.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    die "run scripts/container.bat on Windows; this script is for Linux and WSL" ;;
esac

# --- engine ------------------------------------------------------------------

ENGINE="${MUSIC_CURATOR_CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  for candidate in docker podman; do
    if command -v "$candidate" >/dev/null 2>&1; then ENGINE="$candidate"; break; fi
  done
fi
[ -n "$ENGINE" ] || die "no docker or podman on PATH; install one, or run natively with scripts/setup.sh"
"$ENGINE" compose version >/dev/null 2>&1 \
  || die "$ENGINE has no compose support; install the compose plugin"

# --- arguments ---------------------------------------------------------------

REBUILD=0
# Empty means dist/ in the working tree, so publish lands in the same place
# whether or not --out is given.
OUT_DIR="${MUSIC_CURATOR_OUT:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --rebuild) REBUILD=1; shift ;;
    --out) [ $# -ge 2 ] || die "--out needs a directory"; OUT_DIR="$2"; shift 2 ;;
    --out=*) OUT_DIR="${1#--out=}"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) break ;;
  esac
done

INTERACTIVE=0
SUB="${1:-check}"
CMD=()
case "$SUB" in
  # An empty command leaves compose.yaml's own to run, so the default lives in
  # one place rather than being restated here.
  check) ;;
  validate | lint | test | generate | report | clean) CMD=(scripts/"$SUB".sh "${@:2}") ;;
  # DIR becomes the /out mount, as --out does. Passed through, it would name a
  # directory inside the container, which is discarded on exit.
  publish)
    [ $# -le 2 ] || die "publish takes at most one directory"
    [ $# -lt 2 ] || OUT_DIR="$2"
    CMD=(scripts/publish.sh) ;;
  shell) CMD=(bash); INTERACTIVE=1 ;;
  *) CMD=("$@") ;;
esac

# --- publish directory -------------------------------------------------------
#
# Created here because a mount source has to exist first, and because a
# directory the engine invents belongs to root.

case "$OUT_DIR" in
  "") OUT_DIR="$PWD/dist" ;;
  /*) ;;
  *) OUT_DIR="$CALLER_PWD/$OUT_DIR" ;;
esac
# Checked here as well as in publish.sh, which sees only /out and cannot tell
# that it is a directory inside output/.
if [ "$SUB" = publish ] && publish_dest_refused "$OUT_DIR"; then
  die "refusing to publish to '$(realpath -m "$OUT_DIR")': it is the project root, or inside output/ or reports/"
fi
mkdir -p "$OUT_DIR"
OUT_ABS="$(cd "$OUT_DIR" && pwd)"
export MUSIC_CURATOR_OUT="$OUT_ABS"

# So files written to a bind mount belong to the caller rather than to root.
# A rootless engine maps root in the container to the caller, and any other
# uid to a subordinate one the caller cannot write as.
rootless=false
case "$ENGINE" in
  podman) rootless="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)" ;;
  docker)
    case "$(docker info --format '{{.SecurityOptions}}' 2>/dev/null || true)" in
      *rootless*) rootless=true ;;
    esac ;;
esac
if [ "$rootless" = true ]; then
  MUSIC_CURATOR_UID=0 MUSIC_CURATOR_GID=0
else
  MUSIC_CURATOR_UID="$(id -u)" MUSIC_CURATOR_GID="$(id -g)"
fi
export MUSIC_CURATOR_UID MUSIC_CURATOR_GID MUSIC_CURATOR_IMAGE="$IMAGE"

# --- source exports ----------------------------------------------------------
#
# The README recommends symlinking the two exports into the project root. A
# symlink is resolved inside the container, where its target does not exist, so
# the catalog appears missing however plainly it is listed on the host.

check_export() {
  local name="$1"
  [ -L "$name" ] || return 0
  local target
  target="$(readlink -f "$name" 2>/dev/null || true)"
  case "${target:-}" in
    "$PWD"/*) ;;
    *) say "Note: '$name' is a symlink to ${target:-a missing target}, which is outside the mounted tree; the container will not see it. Copy the file in, or mount its directory." ;;
  esac
}

check_export "Library Playlist.m3u8"
check_export "iPod Sync.m3u8"

# --- image -------------------------------------------------------------------

# The image bakes in the interpreter, so a change to how it is built is a
# change the image cannot pick up on its own.
image_is_stale() {
  local created epoch newest
  created="$("$ENGINE" image inspect -f '{{.Created}}' "$IMAGE" 2>/dev/null)" || return 1
  epoch="$(date -d "$created" +%s 2>/dev/null)" || return 1
  newest="$(stat -c %Y docker/Dockerfile compose.yaml 2>/dev/null | sort -n | tail -1)" || return 1
  [ "${newest:-0}" -gt "$epoch" ]
}

if [ "$REBUILD" -eq 1 ] || ! "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1; then
  say "Building $IMAGE (first run pulls the interpreter; later ones are cached)"
  "$ENGINE" compose build "$SERVICE"
elif image_is_stale; then
  say "Note: $IMAGE is older than the files it was built from; --rebuild refreshes it"
fi

# --- run ---------------------------------------------------------------------

run=("$ENGINE" compose run --rm)

# Without a terminal on both ends, compose refuses to allocate one rather than
# carrying on without it.
if [ "$INTERACTIVE" -eq 0 ] && { [ ! -t 0 ] || [ ! -t 1 ]; }; then
  run+=(-T)
fi

# Named on this host, because the run inside reports its own /out and that path
# exists nowhere the caller can look.
if [ "$SUB" = "publish" ]; then
  say "Publishing to: $OUT_ABS"
fi

exec "${run[@]}" "$SERVICE" ${CMD[@]+"${CMD[@]}"}
