"""Synthetic fixtures.

Tests never depend on the private catalog export, so every
fixture here is small, explicit, and hand-checkable.
"""

from __future__ import annotations

import pytest

from music_engine.core import Track


def make_track(
    artist: str,
    album: str,
    title: str,
    flags: tuple[str, ...] = (),
    track_no: int | None = None,
    disc_no: int | None = None,
) -> Track:
    relative = f"{artist}/{album}/{title}.m4a"
    return Track(
        artist=artist,
        album=album,
        title=title,
        source_path=f"M:\\Music\\{relative}".replace("/", "\\"),
        relative_path=relative,
        flags=frozenset(flags),
        track_no=track_no,
        disc_no=disc_no,
    )


@pytest.fixture
def track_factory():
    return make_track


@pytest.fixture
def tag_config() -> dict:
    return {
        "tags": {
            "music": {},
            "rock": {"parents": ["music"]},
            "jazz": {"parents": ["music"]},
            "college-rock": {"parents": ["rock"]},
            "swing": {"parents": ["jazz"]},
            "big-band": {"parents": ["jazz"]},
        },
        "aliases": {"alternative": "college-rock"},
    }
