"""Data structures shared by the Plex integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Resolution outcomes.
MATCHED = "matched"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"

# How a track was matched, best first. Title alone is never a method.
BY_PATH = "path"
BY_MAPPED_PATH = "mapped-path"
BY_GUID = "guid"
BY_METADATA = "metadata"

CONFIDENCE = {
    BY_PATH: 1.0,
    BY_MAPPED_PATH: 0.95,
    BY_GUID: 0.9,
    BY_METADATA: 0.7,
}


@dataclass(frozen=True)
class PlexTrack:
    """One track as Plex reports it."""

    rating_key: str
    artist: str
    album_artist: str
    album: str
    title: str
    year: int | None = None
    disc_number: int | None = None
    track_number: int | None = None
    duration: int | None = None
    media_path: str = ""
    guid: str = ""
    #: Global listener count from Plex's music metadata provider. Populated on
    #: about 95% of tracks and the strongest available prominence signal.
    rating_count: int = 0
    #: Personal play count on this server.
    view_count: int = 0

    def as_dict(self) -> dict:
        return {
            "rating_key": self.rating_key,
            "artist": self.artist,
            "album_artist": self.album_artist,
            "album": self.album,
            "title": self.title,
            "year": self.year,
            "disc_number": self.disc_number,
            "track_number": self.track_number,
            "duration": self.duration,
            "media_path": self.media_path,
            "guid": self.guid,
            "rating_count": self.rating_count,
            "view_count": self.view_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlexTrack:
        return cls(
            rating_key=str(data["rating_key"]),
            artist=data.get("artist", ""),
            album_artist=data.get("album_artist", ""),
            album=data.get("album", ""),
            title=data.get("title", ""),
            year=data.get("year"),
            disc_number=data.get("disc_number"),
            track_number=data.get("track_number"),
            duration=data.get("duration"),
            media_path=data.get("media_path", ""),
            guid=data.get("guid", ""),
            rating_count=int(data.get("rating_count", 0) or 0),
            view_count=int(data.get("view_count", 0) or 0),
        )


@dataclass(frozen=True)
class ManifestEntry:
    """One track in a generated playlist manifest.

    The manifest is the contract between generation and synchronization: it is
    what the playlist should contain, in order, independent of Plex.
    """

    position: int
    catalog_id: str
    artist: str
    album: str
    title: str
    relative_path: str
    plex_path: str
    disc_no: int | None = None
    track_no: int | None = None
    year: int | None = None

    def as_dict(self) -> dict:
        return {
            "position": self.position,
            "catalog_id": self.catalog_id,
            "artist": self.artist,
            "album": self.album,
            "title": self.title,
            "relative_path": self.relative_path,
            "plex_path": self.plex_path,
            "disc_no": self.disc_no,
            "track_no": self.track_no,
            "year": self.year,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ManifestEntry:
        return cls(
            position=int(data["position"]),
            catalog_id=data["catalog_id"],
            artist=data.get("artist", ""),
            album=data.get("album", ""),
            title=data.get("title", ""),
            relative_path=data.get("relative_path", ""),
            plex_path=data.get("plex_path", ""),
            disc_no=data.get("disc_no"),
            track_no=data.get("track_no"),
            year=data.get("year"),
        )


@dataclass
class Resolution:
    """The outcome of resolving one manifest entry against the Plex index."""

    entry: ManifestEntry
    status: str
    method: str = ""
    plex_track: PlexTrack | None = None
    candidates: list[PlexTrack] = field(default_factory=list)
    detail: str = ""

    @property
    def confidence(self) -> float:
        if self.status != MATCHED:
            return 0.0
        return CONFIDENCE.get(self.method, 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.entry.position,
            "artist": self.entry.artist,
            "title": self.entry.title,
            "album": self.entry.album,
            "relative_path": self.entry.relative_path,
            "status": self.status,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "rating_key": self.plex_track.rating_key if self.plex_track else None,
            "candidate_rating_keys": [c.rating_key for c in self.candidates],
            "detail": self.detail,
        }


@dataclass
class ResolutionSummary:
    """Aggregate resolution result for one playlist."""

    playlist: str
    desired: int = 0
    matched: int = 0
    ambiguous: int = 0
    unresolved: int = 0
    resolutions: list[Resolution] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        return (self.matched / self.desired) if self.desired else 0.0

    @property
    def rating_keys(self) -> list[str]:
        """Matched rating keys, in playlist order."""
        return [
            r.plex_track.rating_key
            for r in self.resolutions
            if r.status == MATCHED and r.plex_track is not None
        ]

    def problems(self) -> list[Resolution]:
        return [r for r in self.resolutions if r.status != MATCHED]

    def as_dict(self, include_details: bool = False) -> dict:
        data = {
            "playlist": self.playlist,
            "desired": self.desired,
            "matched": self.matched,
            "ambiguous": self.ambiguous,
            "unresolved": self.unresolved,
            "match_rate_pct": round(100 * self.match_rate, 2),
        }
        if include_details:
            data["problems"] = [r.as_dict() for r in self.problems()]
        return data


@dataclass
class PlaylistDiff:
    """Difference between a generated playlist and its Plex counterpart."""

    playlist: str
    managed_title: str
    exists: bool = False
    desired: int = 0
    actual: int = 0
    to_add: int = 0
    to_remove: int = 0
    reorder_required: bool = False
    unresolved: int = 0
    ambiguous: int = 0

    @property
    def in_sync(self) -> bool:
        return (
            self.exists
            and self.to_add == 0
            and self.to_remove == 0
            and not self.reorder_required
        )

    def as_dict(self) -> dict:
        return {
            "playlist": self.playlist,
            "managed_title": self.managed_title,
            "exists": self.exists,
            "desired": self.desired,
            "actual": self.actual,
            "to_add": self.to_add,
            "to_remove": self.to_remove,
            "reorder_required": self.reorder_required,
            "unresolved": self.unresolved,
            "ambiguous": self.ambiguous,
            "in_sync": self.in_sync,
        }
