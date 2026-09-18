# Host setup

These steps assume Ubuntu under WSL 2. Only the `/mnt/c` paths are
WSL-specific; the project runs on any Linux or macOS host. To run in a
container instead of a local Python, skip to step 5.

## 1. Prerequisites

Install Git and Python 3.11 or newer:

```bash
sudo apt update
sudo apt install -y git python3
```

Ubuntu ships `venv` and `pip` as separate packages. `./scripts/setup.sh` in
step 4 detects them missing and offers to install them; to install them up
front:

```bash
sudo apt install -y python3-venv python3-pip
```

## 2. Clone into the Linux filesystem

Keep the repository under the Linux home directory, not `/mnt/c`. File access
across the Windows boundary is slow, and every run reads the full catalog
export.

```bash
git clone <REMOTE_URL> ~/wkspace/music-curator
cd ~/wkspace/music-curator
```

## 3. Provide the two source exports

Symlink both exports into the project root:

```bash
ln -s /mnt/c/path/to/'Library Playlist.m3u8' ./
ln -s /mnt/c/path/to/'iPod Sync.m3u8' ./
```

Paths inside the exports must begin with the Windows media root in
`config/settings.yaml`, committed as
`M:\itunes_portable\iTunes\iTunes Media\Music\`. For an export with a
different root, set `catalog.windows_root` and
`sources.rockbox_ipod.windows_root` to match. README.md, under "Source
exports", describes both files and the path layout.

## 4. Run it

Set up the environment once:

```bash
./scripts/setup.sh
```

Then run the full pipeline. The scripts reuse the virtualenv without
activation:

```bash
./scripts/check.sh
```

`check.sh` runs validate, ruff, the test suite, generation and the audit
report. Playlists land in `output/plex/` and `output/rockbox/`.

To use the CLI directly, activate the virtualenv:

```bash
source .venv/bin/activate
music-playlists --help
```

## 5. Run in a container instead

With Docker or Podman, skip the Python packages in step 1 and all of step 4.
The working tree is mounted, so playlists and reports land in `output/` and
`reports/` as on a host run.

```bash
scripts/container.sh            # validate, ruff, test, generate, report
scripts/container.sh generate   # write playlists only
```

On Windows with Docker Desktop or Podman, from cmd, PowerShell or a
double-click:

```bat
scripts\container.bat
scripts\container.bat generate
```

The two exports must be real files in the project root, not symlinks to
`/mnt/c`: a symlink target outside the tree does not exist inside the
container. Both launchers warn about a linked export. README.md, under "In
a container", covers the rest.

## 6. Plex synchronization (optional)

Create `.env` from the template and fill in the URL and token. The file is
gitignored; never commit it.

```bash
cp .env.example .env
```

```dotenv
MUSIC_CURATOR_PLEX_URL=https://192.168.1.10:32400
MUSIC_CURATOR_PLEX_TOKEN=<your token>
MUSIC_CURATOR_PLEX_LIBRARY=Music
```

`MUSIC_CURATOR_PLEX_LIBRARY` defaults to `Music`. README.md, under
"Credentials", lists the optional variables, including the SSL verification
default.

Check the connection, then refresh:

```bash
./scripts/plex.sh status
./scripts/refresh-all.sh          # index, popularity, generate, report, dry run
./scripts/refresh-all.sh --apply  # same, then write to Plex
```

Nothing is written to Plex without `--apply`, and only playlists prefixed
`MC - ` are touched.

## 7. Update workflow

After changing taxonomy, artist profiles or album profiles:

```bash
./scripts/validate.sh     # structural checks, no catalog needed
./scripts/generate.sh     # rewrite playlists
./scripts/report.sh       # overlap, domination, coverage, zero counts
git status
```

Generated playlists, reports, caches and the source exports stay out of Git.
Commit the knowledge files that produced them, plus any documentation the
change invalidated:

```bash
git add taxonomy profiles config docs README.md
git commit
```

Root-level Markdown is gitignored except for the files allow-listed in
`.gitignore`, so a working note in the project root stays untracked.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| "'Library Playlist.m3u8' is missing" or "Library export not found" | The library export is absent from the project root, or a symlink to it is broken. See step 3. |
| "'iPod Sync.m3u8' is missing" | The portable export is absent from the project root. See step 3. |
| "playlist buffer full" on the iPod | The playlist exceeds the device maximum. Raise Rockbox's Settings > General > Playlist > Max entries in playlist, and set `outputs.rockbox.max_tracks` in `config/settings.yaml` to match. |
| Plex command exits 2 | Configuration is incomplete. The error names the variables to set. |
| Plex command exits 3 | The server is unreachable, the certificate was rejected, or a Plex request failed. Check the URL and `MUSIC_CURATOR_PLEX_VERIFY_SSL`. |
