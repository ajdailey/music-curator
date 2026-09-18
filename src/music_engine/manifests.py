"""Canonical playlist manifests.

A manifest is the contract between generation and any destination: the exact
tracks a playlist should contain, in order, described independently of Plex.
Synchronization reads manifests, never the engine's internals.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from .core import Track, normalize_relative_path, safe_name
from .integrations.plex.models import ManifestEntry

MANIFEST_DIRECTORY = "output/manifests"


def catalog_id(track: Track) -> str:
    """Stable identifier for a catalog track: its normalized relative path."""
    return normalize_relative_path(track.relative_path)


def build_entries(tracks: Sequence[Track], plex_root: str) -> list[ManifestEntry]:
    root = plex_root.rstrip("/")
    entries = []
    for position, track in enumerate(tracks, start=1):
        forward = track.relative_path.replace("\\", "/")
        entries.append(
            ManifestEntry(
                position=position,
                catalog_id=catalog_id(track),
                artist=track.artist,
                album=track.album,
                title=track.title,
                relative_path=forward,
                plex_path=f"{root}/{forward}",
                track_no=track.track_no,
                disc_no=track.disc_no,
            )
        )
    return entries


def manifest_hash(entries: Sequence[ManifestEntry]) -> str:
    """Content hash of a manifest, used to detect drift without a diff."""
    payload = "\n".join(f"{e.position}|{e.catalog_id}" for e in entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def write_manifest(
    directory: Path, playlist: str, target: str, entries: Sequence[ManifestEntry]
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_name(playlist)}.json"
    path.write_text(
        json.dumps(
            {
                "playlist": playlist,
                "target": target,
                "track_count": len(entries),
                "manifest_hash": manifest_hash(entries),
                "tracks": [e.as_dict() for e in entries],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def read_manifest(path: Path) -> tuple[str, list[ManifestEntry]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = [ManifestEntry.from_dict(t) for t in data.get("tracks", [])]
    return str(data.get("playlist", path.stem)), entries


def load_manifests(directory: Path) -> dict[str, list[ManifestEntry]]:
    """Every manifest in a directory, keyed by playlist name."""
    if not directory.exists():
        return {}
    manifests = {}
    for path in sorted(directory.glob("*.json")):
        playlist, entries = read_manifest(path)
        manifests[playlist] = entries
    return manifests
