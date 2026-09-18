"""Resolve manifest entries to Plex tracks.

Resolution priority, strongest first:

1. normalized media path
2. mapped media path (catalog root rewritten to the Plex media root)
3. GUID
4. artist + album + disc + track + title; without disc when the manifest
   entry has none (its catalog filename carried no disc prefix)
5. artist + title within the same album

Title alone is never sufficient: a library contains many recordings sharing a
title, and matching on it would silently attach the wrong recording.

Anything matching more than one Plex track is reported AMBIGUOUS rather than
guessed at.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from .models import (
    AMBIGUOUS,
    BY_GUID,
    BY_MAPPED_PATH,
    BY_METADATA,
    BY_PATH,
    MATCHED,
    UNRESOLVED,
    ManifestEntry,
    PlexTrack,
    Resolution,
    ResolutionSummary,
)


def normalize_path(value: str) -> str:
    """Normalize a media path for comparison across platforms."""
    return value.replace("\\", "/").rstrip("/").casefold()


def path_suffix(value: str, depth: int = 3) -> str:
    """The last ``depth`` path segments, which survive differing mount roots.

    Plex may report `/music_portable/Artist/Album/01 Song.m4a` while the
    catalog holds `Artist/Album/01 Song.m4a`. Comparing the tail matches them
    without hardcoding either root.
    """
    parts = [p for p in normalize_path(value).split("/") if p]
    return "/".join(parts[-depth:]) if parts else ""


def _metadata_key(artist: str, album: str, disc, track, title: str) -> tuple:
    return (
        artist.strip().casefold(),
        album.strip().casefold(),
        disc,
        track,
        title.strip().casefold(),
    )


def _discless_key(artist: str, album: str, track, title: str) -> tuple:
    return (artist.strip().casefold(), album.strip().casefold(), track, title.strip().casefold())


class PlexResolver:
    """Indexes Plex tracks and resolves manifest entries against them."""

    def __init__(self, tracks: Iterable[PlexTrack], media_root: str = "/music_portable"):
        self.media_root = "/" + normalize_path(media_root).strip("/")
        self.tracks = list(tracks)

        self._by_path: dict[str, list[PlexTrack]] = defaultdict(list)
        self._by_suffix: dict[str, list[PlexTrack]] = defaultdict(list)
        self._by_guid: dict[str, list[PlexTrack]] = defaultdict(list)
        self._by_metadata: dict[tuple, list[PlexTrack]] = defaultdict(list)
        self._by_discless: dict[tuple, list[PlexTrack]] = defaultdict(list)
        self._by_album_title: dict[tuple, list[PlexTrack]] = defaultdict(list)

        for track in self.tracks:
            if track.media_path:
                self._by_path[normalize_path(track.media_path)].append(track)
                self._by_suffix[path_suffix(track.media_path)].append(track)
            if track.guid:
                self._by_guid[track.guid].append(track)
            artist = track.album_artist or track.artist
            # Plex reports no disc for a single-disc album; that is disc 1.
            disc = track.disc_number if track.disc_number is not None else 1
            self._by_metadata[
                _metadata_key(artist, track.album, disc, track.track_number, track.title)
            ].append(track)
            self._by_discless[
                _discless_key(artist, track.album, track.track_number, track.title)
            ].append(track)
            self._by_album_title[
                (
                    artist.strip().casefold(),
                    track.album.strip().casefold(),
                    track.title.strip().casefold(),
                )
            ].append(track)

    # -- lookup helpers ----------------------------------------------------

    def mapped_path(self, relative_path: str) -> str:
        """Rewrite a catalog-relative path onto the Plex media root."""
        relative = normalize_path(relative_path).lstrip("/")
        return f"{self.media_root}/{relative}"

    def _attempt(self, index: dict, key) -> tuple[str, list[PlexTrack]]:
        candidates = index.get(key, [])
        if len(candidates) == 1:
            return MATCHED, candidates
        if len(candidates) > 1:
            return AMBIGUOUS, candidates
        return UNRESOLVED, []

    # -- resolution --------------------------------------------------------

    def resolve_entry(self, entry: ManifestEntry) -> Resolution:
        if entry.disc_no is not None:
            metadata = (
                self._by_metadata,
                _metadata_key(
                    entry.artist, entry.album, entry.disc_no, entry.track_no, entry.title
                ),
            )
        else:
            metadata = (
                self._by_discless,
                _discless_key(entry.artist, entry.album, entry.track_no, entry.title),
            )
        attempts = (
            (BY_PATH, self._by_path, normalize_path(entry.plex_path)),
            (BY_MAPPED_PATH, self._by_path, self.mapped_path(entry.relative_path)),
            (BY_MAPPED_PATH, self._by_suffix, path_suffix(entry.relative_path)),
            (
                BY_GUID,
                self._by_guid,
                entry.catalog_id if entry.catalog_id.startswith("plex://") else "",
            ),
            (BY_METADATA, *metadata),
            (
                BY_METADATA,
                self._by_album_title,
                (
                    entry.artist.strip().casefold(),
                    entry.album.strip().casefold(),
                    entry.title.strip().casefold(),
                ),
            ),
        )

        ambiguous: list[PlexTrack] = []
        for method, index, key in attempts:
            if not key:
                continue
            status, candidates = self._attempt(index, key)
            if status == MATCHED:
                return Resolution(
                    entry=entry, status=MATCHED, method=method, plex_track=candidates[0]
                )
            if status == AMBIGUOUS and not ambiguous:
                ambiguous = candidates

        if ambiguous:
            return Resolution(
                entry=entry,
                status=AMBIGUOUS,
                candidates=ambiguous,
                detail=f"{len(ambiguous)} Plex tracks match; refusing to guess",
            )
        return Resolution(
            entry=entry,
            status=UNRESOLVED,
            detail="No Plex track matched by path, GUID, or metadata",
        )

    def resolve(self, playlist: str, entries: Sequence[ManifestEntry]) -> ResolutionSummary:
        summary = ResolutionSummary(playlist=playlist, desired=len(entries))
        for entry in entries:
            resolution = self.resolve_entry(entry)
            summary.resolutions.append(resolution)
            if resolution.status == MATCHED:
                summary.matched += 1
            elif resolution.status == AMBIGUOUS:
                summary.ambiguous += 1
            else:
                summary.unresolved += 1
        return summary
