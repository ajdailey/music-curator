"""The song popularity knowledge file.

Generation must stay deterministic and offline, and Plex is a destination
rather than a source. So popularity data is not read from Plex at generation
time. It is exported once into `knowledge/song-popularity.json`, and generation
reads that file like any other knowledge file.

The data itself comes from Plex's music metadata provider: `ratingCount` is a
global listener count, populated on roughly 85-95% of the library. Measured
against the real catalog it ranks Stone Temple Pilots as Plush (856k),
Interstate Love Song (704k), Creep (590k), which is the correct order.

A song exists in many copies across editions and live albums, and the listener
count is attached inconsistently to them. The maximum across all copies of a
song is taken as that song's figure.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .versions import base_title

SCHEMA_VERSION = 1
DEFAULT_PATH = "knowledge/song-popularity.json"


def song_key(identity: str, title: str) -> str:
    return f"{identity.casefold()}|{base_title(title)}"


@dataclass
class PopularityData:
    """Per-song popularity figures, independent of any particular source."""

    songs: dict[str, dict] = field(default_factory=dict)
    source: str = ""
    library: str = ""
    generated_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def listeners(self, identity: str, title: str) -> int:
        return int(self.songs.get(song_key(identity, title), {}).get("listeners", 0))

    def plays(self, identity: str, title: str) -> int:
        return int(self.songs.get(song_key(identity, title), {}).get("plays", 0))

    @property
    def covered_songs(self) -> int:
        return sum(1 for row in self.songs.values() if row.get("listeners"))

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "library": self.library,
            "generated_at": self.generated_at,
            "song_count": len(self.songs),
            "songs_with_listeners": self.covered_songs,
            "songs": self.songs,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> PopularityData:
        return cls(
            songs=dict(data.get("songs", {})),
            source=str(data.get("source", "")),
            library=str(data.get("library", "")),
            generated_at=float(data.get("generated_at", 0.0)),
            schema_version=int(data.get("schema_version", 0)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> PopularityData | None:
        """Read the knowledge file, or None when it is absent or unusable.

        An unreadable, corrupt, or other-schema file prints a warning on stderr
        and is not used, so scoring falls back to the re-release heuristic.
        """
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            loaded = cls.from_dict(data)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            _warn(f"{path} is unreadable ({error}); using the re-release heuristic")
            return None
        if loaded.schema_version != SCHEMA_VERSION:
            _warn(
                f"{path} has schema_version {loaded.schema_version}, expected "
                f"{SCHEMA_VERSION}; using the re-release heuristic. Re-export it."
            )
            return None
        return loaded


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def build_from_plex_tracks(
    tracks: Iterable,
    identity_of: Mapping[str, str] | None = None,
    source: str = "plex",
    library: str = "",
    generated_at: float = 0.0,
) -> PopularityData:
    """Derive popularity from indexed Plex tracks.

    Takes the maximum listener count across every copy of a song, since the
    figure is attached inconsistently across editions, and the maximum play
    count likewise.
    """
    resolve = (identity_of or {}).get
    listeners: dict[str, int] = defaultdict(int)
    plays: dict[str, int] = defaultdict(int)
    titles: dict[str, str] = {}

    for track in tracks:
        artist = getattr(track, "album_artist", "") or getattr(track, "artist", "")
        identity = resolve(artist, artist)
        key = song_key(identity, getattr(track, "title", ""))
        listeners[key] = max(listeners[key], int(getattr(track, "rating_count", 0) or 0))
        plays[key] = max(plays[key], int(getattr(track, "view_count", 0) or 0))
        titles.setdefault(key, getattr(track, "title", ""))

    songs = {
        key: {"listeners": listeners[key], "plays": plays[key], "title": titles.get(key, "")}
        for key in listeners
        if listeners[key] or plays[key]
    }
    return PopularityData(
        songs=songs,
        source=source,
        library=library,
        generated_at=generated_at,
    )
