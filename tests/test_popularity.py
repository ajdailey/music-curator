"""The song popularity knowledge file."""

from __future__ import annotations

import json

from music_engine.integrations.plex.models import PlexTrack
from music_engine.popularity import (
    SCHEMA_VERSION,
    PopularityData,
    build_from_plex_tracks,
    song_key,
)


def track(artist, album, title, listeners=0, plays=0):
    return PlexTrack(
        rating_key="1",
        artist=artist,
        album_artist=artist,
        album=album,
        title=title,
        rating_count=listeners,
        view_count=plays,
    )


def test_builds_listener_counts_per_song():
    data = build_from_plex_tracks([track("STP", "Purple", "Plush", 856262)])
    assert data.listeners("STP", "Plush") == 856262


def test_takes_the_maximum_across_copies_of_a_song():
    """Plex attaches the figure inconsistently across editions."""
    data = build_from_plex_tracks(
        [
            track("STP", "Original Album Series", "Interstate Love Song", 0),
            track("STP", "Alive In The Windy City", "Interstate Love Song", 703657),
            track("STP", "Purple", "Interstate Love Song", 704161),
        ]
    )
    assert data.listeners("STP", "Interstate Love Song") == 704161


def test_version_markers_do_not_split_a_song():
    data = build_from_plex_tracks(
        [
            track("STP", "Core", "Plush (2017 Remaster)", 856262),
            track("STP", "Live", "Plush (live)", 859259),
        ]
    )
    assert data.listeners("STP", "Plush") == 859259


def test_alias_spellings_share_one_entry():
    data = build_from_plex_tracks(
        [track("R.E.M_", "Murmur", "Radio Free Europe", 500000)],
        identity_of={"R.E.M_": "R.E.M."},
    )
    assert data.listeners("R.E.M.", "Radio Free Europe") == 500000


def test_songs_without_any_signal_are_omitted():
    data = build_from_plex_tracks([track("A", "Al", "Nothing Known", 0, 0)])
    assert data.songs == {}


def test_play_counts_are_captured():
    data = build_from_plex_tracks([track("A", "Al", "Song", 0, 7)])
    assert data.plays("A", "Song") == 7


def test_round_trips_through_disk(tmp_path):
    data = build_from_plex_tracks([track("A", "Al", "Song", 1234, 5)], library="Portable")
    path = tmp_path / "pop.json"
    data.save(path)
    loaded = PopularityData.load(path)
    assert loaded.listeners("A", "Song") == 1234
    assert loaded.library == "Portable"


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "pop.json"
    path.write_text("{not json", encoding="utf-8")
    assert PopularityData.load(path) is None


def test_old_schema_is_ignored(tmp_path):
    path = tmp_path / "pop.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION - 1}), encoding="utf-8")
    assert PopularityData.load(path) is None


def test_missing_file_is_ignored(tmp_path):
    assert PopularityData.load(tmp_path / "absent.json") is None


def test_song_key_is_stable_across_case_and_version_markers():
    assert song_key("STP", "Plush") == song_key("stp", "Plush (Live at Leeds)")


def test_coverage_counts_songs_with_listener_data():
    data = build_from_plex_tracks(
        [track("A", "Al", "Known", 100), track("A", "Al", "Played", 0, 3)]
    )
    assert len(data.songs) == 2
    assert data.covered_songs == 1


def test_old_schema_file_warns_and_is_not_used(tmp_path, capsys):
    path = tmp_path / "song-popularity.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1, "songs": {}}))
    assert PopularityData.load(path) is None
    assert "schema_version" in capsys.readouterr().err


def test_corrupt_file_warns_and_is_not_used(tmp_path, capsys):
    path = tmp_path / "song-popularity.json"
    path.write_text("{not json")
    assert PopularityData.load(path) is None
    assert "unreadable" in capsys.readouterr().err


def test_absent_file_is_silent(tmp_path, capsys):
    assert PopularityData.load(tmp_path / "absent.json") is None
    assert capsys.readouterr().err == ""
