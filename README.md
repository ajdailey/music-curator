# Music Curator

[![CI](https://github.com/ajdailey/music-curator/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ajdailey/music-curator/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/ajdailey/music-curator)](LICENSE)

Music Curator generates Plex playlists from a full library export and Rockbox
playlists from the subset present on a portable device. Curation decisions live
in YAML knowledge files; the Python package applies them.

## A personal engine

This repository curates one collection. The knowledge files in `taxonomy/`,
`profiles/` and `config/` record that collector's classifications and taste,
and the track counts quoted in `docs/` are measured against that library. The
code holds no library-specific knowledge, so the engine runs against another
collection given that collection's two exports and its own knowledge files.
The committed files are a worked example of the schema, not a starter set:
against a different library a profile matches only an artist the two libraries
share, and `report` ranks the unclassified artists by track count.

The test suite runs on synthetic fixtures and needs neither the exports nor a
Plex server. CI (`.github/workflows/ci.yml`) runs ruff, `validate` and the
tests on Linux and Windows at Python 3.11 and 3.14; generation needs the
exports and does not run there.

## Source exports

Two machine-specific files must be present in the project root. Both are
gitignored, and the tools never modify either one.

| File | Role |
| --- | --- |
| `Library Playlist.m3u8` | The full library export, read directly as the catalog; there is no snapshot. Replace this file to refresh the catalog. |
| `iPod Sync.m3u8` | The portable library. Rockbox output is the intersection with it. |

Each entry is a Windows path laid out as `Artist\Album\[disc-]track
Title.ext`, and the catalog is parsed from those path segments. Entries must
begin with the root configured in `config/settings.yaml`:
`catalog.windows_root` for the library export and
`sources.rockbox_ipod.windows_root` for the portable one.

Symlinks work on a host run. A container run cannot follow them; see
[In a container](#in-a-container).

```bash
ln -s /path/to/'Library Playlist.m3u8' ./
ln -s /path/to/'iPod Sync.m3u8' ./
```

## Setup

`scripts/setup.sh` runs once per machine and is safe to re-run. It checks for
Python 3.11 or newer, installs the system packages `venv` needs, creates
`.venv`, and installs the package with the `plex` and `dev` extras.

```bash
./scripts/setup.sh          # prompts before installing system packages
./scripts/setup.sh --yes    # no prompt
./scripts/setup.sh --check  # report what is missing, change nothing
```

On Windows, from a command prompt in the project root:

```bat
scripts\setup.bat
scripts\setup.bat --check
```

Setup is optional on a host that can already build virtualenvs: the scripts in
`scripts/` create `.venv` and install the package on first use, reinstall
when `pyproject.toml` is newer than the last install, and rebuild a virtualenv
whose interpreter fails to start. Debian and Ubuntu ship `venv` and `pip` as
separate packages; without them `python3 -m venv` stops
with "ensurepip is not available".

## Running

The shell scripts need no activated virtualenv:

```bash
./scripts/check.sh       # validate, ruff, pytest, generate, report
./scripts/validate.sh    # knowledge-file validation, no catalog needed
./scripts/lint.sh        # ruff; --fix applies the fixable findings
./scripts/test.sh        # test suite
./scripts/generate.sh    # write playlists
./scripts/report.sh      # audit report
```

`check.sh`, `generate.sh` and `report.sh` exit with an error naming the missing
file when either source export is absent.

The equivalent CLI, with the virtualenv active (`source .venv/bin/activate`,
or `.venv\Scripts\activate` on Windows, where the shell scripts do not run):

```bash
music-playlists validate
music-playlists generate
music-playlists report
pytest
```

### In a container

The container replaces a local Python, including on a Windows host without
WSL. `compose.yaml` holds the whole configuration (mounts, caches,
environment) and both platforms run that same file; the launchers add argument
handling only.

```bash
scripts/container.sh [--rebuild] [--out DIR] [check|validate|lint|test|generate|report|clean|publish [DIR]|shell]
```

```bat
scripts\container.bat generate         REM cmd, PowerShell, or a double-click
```

```bash
docker compose run --rm curator        # check, on either platform
```

- Both launchers use Docker or Podman with compose support, chosen by
  `MUSIC_CURATOR_CONTAINER_ENGINE` or by whichever is on `PATH`, Docker first.
  `MUSIC_CURATOR_IMAGE` names the image (default `music-curator:local`) for
  the launchers and for `compose.yaml`. Run `container.bat` from Windows
  shells; `container.sh` refuses to run under MSYS, MinGW or Cygwin.
- The working tree is bind-mounted, so playlists land in `output/` and reports
  in `reports/` as on a host run. `publish` copies both to `dist/`, or to the
  directory given as `publish DIR`, `--out DIR` or `MUSIC_CURATOR_OUT`, which
  becomes the `/out` mount. A relative directory resolves against the current
  directory. Both launchers refuse the project root and any directory inside
  `output/` or `reports/`.
- The container keeps its virtualenv in a named volume, not in `.venv`, so a
  container run never overwrites the host interpreter on the same tree.
- The container runs as `MUSIC_CURATOR_UID`:`MUSIC_CURATOR_GID`, default
  `0:0`. On Linux `container.sh` sets the calling user's ids for a rootful
  engine and `0:0` for a rootless one, which maps root to the caller. Invoking
  compose directly on Linux with rootful Docker needs both variables exported
  (`$(id -u)`, `$(id -g)`), or the files written to the tree belong to root.
  Docker Desktop maps ownership itself.
- Plex credentials come from the gitignored `.env` on the bind mount. No secret
  passes through `compose.yaml`, the image or the shell environment.
- A symlinked `Library Playlist.m3u8` or `iPod Sync.m3u8` whose target is
  outside the tree does not resolve inside the container. Copy the export into
  the project root, or mount its directory. Both launchers warn before
  running.
- `container.sh` builds the image on first use and reports when
  `docker/Dockerfile` or `compose.yaml` is newer than the image; `--rebuild`
  rebuilds it.

## Command behavior

| Command | Behavior |
| --- | --- |
| `validate` | Checks the knowledge files structurally. Errors: undeclared tags, unknown ordering strategies, unknown playlist, ordering or balancing keys, duplicate or missing playlist names, playlist names that share an output filename, out-of-range `max_share_per_artist`, unknown `clear_tags` or `clear_flags` names in album entries, and identity collisions. A collection with balancing enabled, and a playlist tag no artist carries, are warnings. Exits non-zero on errors. |
| `generate` | Writes playlists and manifests, prints per-playlist counts, and writes `reports/generation-summary.json`. Fails on the key, filename and album-entry errors `validate` reports. A playlist with no tracks for a target is not written for it, and any `.m3u8` or manifest the current taxonomy does not produce is removed. An unreadable `knowledge/song-popularity.json` prints a warning and is ignored. |
| `report` | Writes `reports/audit-report.json` and prints a summary: zero-count explanations, playlist overlap, artist domination, remix-heavy playlists, portable coverage, version statistics, measured popularity coverage and taxonomy gaps. |

The ruff rule selection in `pyproject.toml` is explicit because ruff's default
rule set widens with each release.

## Version preference

Where the catalog holds several recordings of one song, generation keeps one.
A reprise or an instrumental is a separate song, not a version. The
performance order is studio, radio edit, live, alternate take, demo, remix.
A remix is a different arrangement, so it sorts last and never substitutes for
the original.

Balanced playlists cap remixes at `max_remix_share` in `config/settings.yaml`:
0.05 for most playlist types, 0.0 for mood and curated. The cap applies only to
songs the catalog holds solely as a remix, since a remix with an original
present has already lost to it.

To exclude remixes from one playlist regardless of what else exists, add to its
definition:

```yaml
exclude_flags: [remix]
```

`live`, `demo`, `acoustic`, `deluxe`, `remaster` and `compilation` work the
same way. `report` lists every playlist that is 10 percent or more remixes.

## Plex synchronization

Plex is a destination, never a source. Synchronization does not affect
generation, taxonomy, sequencing or catalog data.

### Credentials

Credentials come only from the environment. Copy `.env.example` to `.env`
(gitignored) or export the variables; a variable set in the environment
overrides `.env`.

| Variable | Default |
| --- | --- |
| `MUSIC_CURATOR_PLEX_URL` | required |
| `MUSIC_CURATOR_PLEX_TOKEN` | required |
| `MUSIC_CURATOR_PLEX_LIBRARY` | `Music` |
| `MUSIC_CURATOR_PLEX_VERIFY_SSL` | false for an https URL, true otherwise |
| `MUSIC_CURATOR_PLEX_MEDIA_ROOT` | `/music_portable` |

A Plex command with a required variable missing exits with status 2 and names
every missing variable. The token is never logged, written to a report or
committed. Verification defaults to off for https because a Plex server on a
private address usually presents a self-signed certificate; `plex status`
prints the `verify_ssl` value in effect.

### Running a synchronization

`scripts/plex-sync.sh` runs status, index, resolve, diff, then sync. Each stage
must succeed before the next runs, and every stage before sync is read-only
against Plex.

```bash
./scripts/plex-sync.sh                        # full dry run, writes nothing
./scripts/plex-sync.sh --apply                # dry run first, then write
./scripts/plex-sync.sh --playlist "Grunge"    # one playlist
./scripts/plex-sync.sh --refresh-index        # rebuild the library index
./scripts/plex-sync.sh --generate             # regenerate playlists first
```

The script stops if `output/manifests/` is empty; run `generate.sh` or pass
`--generate`. The index stage resumes a partial index. `--apply` stops when the
dry run refuses any playlist; `reports/plex-sync.json` lists the refusals.

`plex sync` continues past a playlist whose Plex request fails and records the
error in `reports/plex-sync.json`. It exits 1 when any playlist was refused,
failed, or was written but failed verification. A Plex request error anywhere
else in a Plex command exits 3, as an unreachable server does.

Writes are gated by:

- the `MC - ` prefix: synchronization modifies or deletes no other playlist;
- the explicit `--apply` flag;
- resolution thresholds, which refuse a playlist above 5 percent unresolved or
  2 percent ambiguous tracks (`plex sync --max-unresolved`, `--max-ambiguous`);
- a refusal to replace a populated playlist with an empty one;
- a staged swap: the playlist is built as `<title> (syncing)` and verified,
  the live playlist is renamed to `<title> (previous)`, the staged one is
  renamed into place, and `(previous)` is deleted last. A failure leaves the
  old playlist intact under one of the two names, and a rerun removes the
  leftover copies.

`plex sync --create-only` never modifies an existing playlist.

### Song popularity

Plex's music metadata carries a global listener count on most tracks.
Exporting it gives generation a measured prominence signal in place of the
re-release heuristic:

```bash
./scripts/plex.sh index            # once; see Library index below
./scripts/plex.sh popularity       # writes knowledge/song-popularity.json
./scripts/generate.sh              # playlists now use it
```

Generation reads the knowledge file, never Plex, so playlists stay
deterministic and buildable offline. A song with no measured figure falls back
to the re-release heuristic, capped so an inferred score never outranks a
measured one. The file is gitignored derived data; generation works without
it.

Measured against this library and its live Plex server, Interstate Love Song
ranks 18th of 50 Stone Temple Pilots songs on the heuristic and 2nd on
listener data.

### Library index

The index is cached in `.cache/plex-index.json` and checkpointed, so an
interrupted run resumes. Indexing measured roughly 600-700 tracks per second
against this library's Plex server, so the cost scales with catalog size.

```bash
./scripts/plex.sh index            # build, or report a cached or partial index
./scripts/plex.sh index --resume   # continue after an interruption
./scripts/plex.sh index --refresh  # rebuild from scratch
```

An index run that ends short of the library's reported track total saves the
index as incomplete and exits 1; rebuild it with `--refresh`. `--resume`
refuses a partial index from another server or library.

Resolution refuses a partial index, since a track not yet fetched would be
reported as unresolved. It also refuses an index built for a different
`MUSIC_CURATOR_PLEX_LIBRARY`, and `diff` and `sync` refuse one built on a
different server.

The individual stages:

```bash
./scripts/plex.sh status              # connection, auth, libraries
./scripts/plex.sh index --refresh     # cache the Plex library
./scripts/plex.sh resolve             # match manifests to Plex (no writes)
./scripts/plex.sh diff                # compare with Plex (no writes)
./scripts/plex.sh sync                # dry run
./scripts/plex.sh sync --apply        # write
```

### Last.fm enrichment

Last.fm fills listener counts that Plex metadata lacks. It is off by default
and never runs as part of generate, report or sync. It requires
`MUSIC_CURATOR_LASTFM_API_KEY` and is rate limited to 5 requests per second;
`MUSIC_CURATOR_LASTFM_RATE_LIMIT` can lower the rate but not raise it. A full
backfill takes far longer than the Plex index.

```bash
./scripts/lastfm.sh enrich --dry-run     # report the gap, no requests
./scripts/lastfm.sh enrich --limit 200   # fill a bounded number
./scripts/lastfm.sh enrich               # fill the whole gap
```

Hits and "track not found" answers are cached in `.cache/lastfm.json`; a failed request
is not, and is retried on the next run. `enrich` exits 2 when Last.fm rejects
the key, and exits 1 when any lookup failed (the summary's `failed` count) or
after 10 consecutive failures, where it stops. Progress is saved in every case.

Enrichment writes into `knowledge/song-popularity.json`; run `generate.sh`
afterwards.

## Refresh, clean and publish

```bash
./scripts/refresh-all.sh                   # index, popularity, generate, report, Plex dry run
./scripts/refresh-all.sh --apply           # same, then write to Plex
./scripts/refresh-all.sh --skip-index      # reuse the cached index
./scripts/refresh-all.sh --playlist NAME   # limit the Plex sync to one playlist
./scripts/clean.sh                         # remove output, manifests and reports
./scripts/clean.sh --cache                 # also .cache/: Plex index, Last.fm cache
./scripts/clean.sh --all                   # also the popularity knowledge file
./scripts/clean.sh --dry-run               # list what would be removed
./scripts/publish.sh /media/card           # copy output/ and reports/ elsewhere
```

`clean.sh` never touches the source exports or the curated knowledge files. An
ordinary clean keeps `knowledge/song-popularity.json`, since rebuilding it
needs a full index.

`publish.sh` defaults to `MUSIC_CURATOR_OUT_DIR`, else `dist/` under the
project root; a relative destination resolves against the current directory.
It replaces the `output/` and `reports/` subdirectories of the destination and
leaves anything else there, so it refuses `/`, the project root and any
directory inside `output/` or `reports/`.

## Outputs

| Path | Contents |
| --- | --- |
| `output/plex/` | Playlists over the full library. |
| `output/rockbox/` | The same playlists intersected with the portable library. |
| `output/manifests/` | Playlist manifests, the input to synchronization. |
| `reports/generation-summary.json` | Per-playlist counts. |
| `reports/audit-report.json` | The full audit. |

Rockbox output is always a subset of Plex output for the same playlist.

## Editing the knowledge base

Adding a playlist means editing YAML, not Python:

| File | Contains |
| --- | --- |
| `taxonomy/tags.yaml` | The tag hierarchy |
| `taxonomy/playlists.yaml` | Playlist membership, sequencing, balancing |
| `profiles/artists.yaml` | Artist classifications |
| `profiles/identities.yaml` | Artist identities, aliases, relationships |
| `profiles/albums.yaml` | Album patterns and per-album entries |
| `config/settings.yaml` | Sources, outputs, balancing policy, overlap pairs |

Run `./scripts/validate.sh` after editing, then `./scripts/report.sh` to see
the effect on overlap, domination and coverage.

## Repository documents

| Document | Covers |
| --- | --- |
| `MUSICAL_CONSTITUTION.md` | Curation principles |
| `docs/VISION.md` | Design intent and long-term direction |
| `docs/DESIGN.md` | Pipeline, algorithms, invariants |
| `docs/CLASSIFICATION_NOTES.md` | Classification rationale and open questions |
| `ROADMAP.md` | Planned work |
| `WSL_SETUP.md` | Host setup, step by step |

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
