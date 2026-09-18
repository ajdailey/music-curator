"""Local cache of the Plex library index.

Indexing a large music library is slow, so the track list is cached on disk.
The cache records which server and library it came from and refuses to be used
against a different one, which prevents silently resolving against the wrong
library after a configuration change.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import PlexTrack

SCHEMA_VERSION = 2
DEFAULT_CACHE_PATH = ".cache/plex-index.json"


@dataclass
class PlexIndex:
    """A cached snapshot of every track in the Plex music library."""

    tracks: list[PlexTrack] = field(default_factory=list)
    created_at: float = 0.0
    server_identifier: str = ""
    library: str = ""
    schema_version: int = SCHEMA_VERSION
    #: False while an index is still being built, so an interrupted run can be
    #: resumed instead of restarted. Resolution refuses a partial index.
    complete: bool = True
    next_start: int = 0
    expected_total: int = 0

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at) if self.created_at else 0.0

    @property
    def age_hours(self) -> float:
        return self.age_seconds / 3600

    def matches(self, server_identifier: str, library: str) -> bool:
        return (
            self.schema_version == SCHEMA_VERSION
            and self.server_identifier == server_identifier
            and self.library == library
        )

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "server_identifier": self.server_identifier,
            "library": self.library,
            "track_count": len(self.tracks),
            "complete": self.complete,
            "next_start": self.next_start,
            "expected_total": self.expected_total,
            "tracks": [t.as_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlexIndex:
        return cls(
            tracks=[PlexTrack.from_dict(t) for t in data.get("tracks", [])],
            created_at=float(data.get("created_at", 0.0)),
            server_identifier=str(data.get("server_identifier", "")),
            library=str(data.get("library", "")),
            schema_version=int(data.get("schema_version", 0)),
            complete=bool(data.get("complete", True)),
            next_start=int(data.get("next_start", 0)),
            expected_total=int(data.get("expected_total", 0)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temporary file so an interrupted write cannot leave a
        # half-written cache that later parses as valid.
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.as_dict(), indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> PlexIndex | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        index = cls.from_dict(data)
        if index.schema_version != SCHEMA_VERSION:
            return None
        return index


def cache_path(project_root: Path, configured: str | None = None) -> Path:
    return project_root / (configured or DEFAULT_CACHE_PATH)
