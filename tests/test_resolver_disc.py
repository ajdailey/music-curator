"""Disc number in the resolver's metadata tier."""

from __future__ import annotations

from music_engine.integrations.plex.models import (
    AMBIGUOUS,
    BY_METADATA,
    MATCHED,
    ManifestEntry,
    PlexTrack,
)
from music_engine.integrations.plex.resolver import PlexResolver


def plex(key: str, disc: int | None, title: str = "Intro") -> PlexTrack:
    return PlexTrack(
        rating_key=key,
        artist="A",
        album_artist="A",
        album="Box",
        title=title,
        disc_number=disc,
        track_number=1,
    )


def entry(disc: int | None) -> ManifestEntry:
    return ManifestEntry(
        position=1,
        catalog_id="a/box/x.m4a",
        artist="A",
        album="Box",
        title="Intro",
        relative_path="unmatched/path.m4a",
        plex_path="/elsewhere/unmatched/path.m4a",
        disc_no=disc,
        track_no=1,
    )


def test_disc_number_separates_same_track_on_two_discs():
    """Track 1 of disc 2 must not resolve to track 1 of disc 1."""
    resolver = PlexResolver([plex("1", 1), plex("2", 2)])
    resolution = resolver.resolve_entry(entry(2))
    assert resolution.status == MATCHED
    assert resolution.method == BY_METADATA
    assert resolution.plex_track.rating_key == "2"


def test_plex_track_without_disc_is_disc_one():
    resolver = PlexResolver([plex("1", None)])
    assert resolver.resolve_entry(entry(1)).plex_track.rating_key == "1"


def test_entry_without_disc_matches_any_single_candidate():
    resolver = PlexResolver([plex("2", 2)])
    assert resolver.resolve_entry(entry(None)).plex_track.rating_key == "2"


def test_entry_without_disc_is_ambiguous_across_discs():
    resolver = PlexResolver([plex("1", 1), plex("2", 2)])
    assert resolver.resolve_entry(entry(None)).status == AMBIGUOUS
