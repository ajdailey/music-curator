"""Catalog loading.

The library export (``Library Playlist.m3u8``) is the single source of truth.
There is no derived snapshot: parsing the export directly keeps the catalog
from drifting from the library it came from, and refreshing the catalog means
replacing the export file.

Every field is recovered from the export:

* ``artist`` / ``album``   - the first two path segments
* ``disc_no`` / ``track_no`` / ``title`` - the filename ("1-01 Humph")
* ``flags``                - derived from album and title text

Filenames are authoritative. The ``#EXTINF`` display line is not parsed for
artist or title: it is ambiguous whenever a title itself contains " - ".
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from .core import Track
from .versions import LIVE_TEXT, REMIX_TEXT, marker

# "01 Title", "1-01 Title", "1.01 Title" - optional disc prefix, then track.
_TRACK_FILENAME = re.compile(r"^(?:(?P<disc>\d+)[-.])?(?P<track>\d+)[\s._-]+(?P<title>.+)$")

# Version markers derived from album and title text.
_FLAG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("live", LIVE_TEXT),
    ("deluxe", marker(r"deluxe")),
    ("remaster", marker(r"remaster(?:ed)?")),
    ("compilation", marker(r"greatest\s+hits|best\s+of|anthology|essential|compilation")),
    ("demo", marker(r"demos?")),
    ("acoustic", marker(r"acoustic")),
    # Shared with versions.classify so a playlist excluding the `remix` flag and
    # the balancer's remix quota always agree on what a remix is.
    ("remix", REMIX_TEXT),
)


def derive_flags(album: str, title: str) -> frozenset[str]:
    """Derive version flags from album and title text."""
    haystack = f"{album} | {title}"
    return frozenset(name for name, pattern in _FLAG_PATTERNS if pattern.search(haystack))


def parse_filename(stem: str) -> tuple[int | None, int | None, str]:
    """Split a filename stem into (disc_no, track_no, title)."""
    match = _TRACK_FILENAME.match(stem)
    if not match:
        return None, None, stem
    disc = match.group("disc")
    return (
        int(disc) if disc else None,
        int(match.group("track")),
        match.group("title").strip(),
    )


def iter_library_playlist(path: Path, windows_root: str) -> Iterator[Track]:
    """Parse a library M3U/M3U8 export into tracks."""
    if not path.exists():
        raise FileNotFoundError(f"Library export not found: {path}")
    root_norm = windows_root.replace("/", "\\").rstrip("\\") + "\\"
    root_fold = root_norm.casefold()

    with path.open(encoding="utf-8-sig", errors="replace") as handle:
        for raw in handle:
            value = raw.strip()
            if not value or value.startswith("#"):
                continue

            win = value.replace("/", "\\")
            if not win.casefold().startswith(root_fold):
                # Outside the configured library root; cannot be mapped safely.
                continue
            relative = win[len(root_norm):]
            segments = relative.split("\\")
            if len(segments) < 2:
                continue

            artist = segments[0]
            album = segments[1] if len(segments) > 2 else ""
            filename = segments[-1]
            stem, _, _extension = filename.rpartition(".")
            stem = stem or filename
            disc_no, track_no, title = parse_filename(stem)

            yield Track(
                artist=artist,
                album=album,
                title=title,
                source_path=win,
                relative_path=relative,
                flags=derive_flags(album, title),
                track_no=track_no,
                disc_no=disc_no,
            )


def read_catalog(path: Path, windows_root: str) -> list[Track]:
    """Load the full catalog from a library export."""
    return list(iter_library_playlist(path, windows_root))
