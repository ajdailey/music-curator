"""Sequencing: deterministic ordering, collection album grouping, interleaving."""

from __future__ import annotations

import pytest

from music_engine.sequencing import (
    KNOWN_STRATEGIES,
    album_order,
    deterministic_order,
    interleaved_order,
    sequence_tracks,
    space_duplicates,
)
from music_engine.versions import base_title
from tests.conftest import make_track


def t(artist: str, album: str, title: str, track_no: int | None = None):
    return make_track(artist, album, title, track_no=track_no)


# --- interleaving ---------------------------------------------------------


def test_interleaving_is_stable_and_avoids_artist_blocks():
    rows = [t("A", "One", f"A{i}") for i in range(4)] + [
        t("B", "Two", f"B{i}") for i in range(4)
    ]
    first = interleaved_order(rows, "seed")
    second = interleaved_order(rows, "seed")
    assert [x.relative_path for x in first] == [x.relative_path for x in second]
    assert all(first[i].artist != first[i + 1].artist for i in range(len(first) - 1))


def test_interleaving_preserves_every_track():
    rows = [t("A", "One", f"A{i}") for i in range(5)] + [
        t("B", "Two", f"B{i}") for i in range(3)
    ]
    assert len(interleaved_order(rows, "seed")) == 8


def test_interleaving_is_independent_of_input_order():
    rows = [t("A", "One", f"A{i}") for i in range(3)] + [
        t("B", "Two", f"B{i}") for i in range(3)
    ]
    forward = interleaved_order(rows, "seed")
    backward = interleaved_order(list(reversed(rows)), "seed")
    assert [x.relative_path for x in forward] == [x.relative_path for x in backward]


def test_different_seeds_produce_different_orders():
    rows = [t("A", "One", f"A{i}") for i in range(6)] + [
        t("B", "Two", f"B{i}") for i in range(6)
    ]
    assert [x.title for x in interleaved_order(rows, "s1")] != [
        x.title for x in interleaved_order(rows, "s2")
    ]


def test_interleaving_drains_a_single_artist_without_hanging():
    rows = [t("Solo", "Album", f"S{i}") for i in range(5)]
    assert len(interleaved_order(rows, "seed")) == 5


def test_interleaving_rotates_albums_within_an_artist():
    rows = [t("A", "One", f"X{i}") for i in range(3)] + [
        t("A", "Two", f"Y{i}") for i in range(3)
    ]
    albums = [x.album for x in interleaved_order(rows, "seed")]
    assert albums[0] != albums[1]


def test_interleaving_prefers_studio_material_within_an_album():
    """Sequencing prioritizes studio takes inside each album group.

    Global studio preference is the balancing layer's job (prefer_studio and
    max_live_share); sequencing only orders what it is handed.
    """
    live = [make_track("A", "Mixed", f"L{i}", flags=("live",)) for i in range(3)]
    studio = [make_track("A", "Mixed", f"S{i}") for i in range(3)]
    ordered = interleaved_order(live + studio, "seed")
    first_live = next(i for i, x in enumerate(ordered) if "live" in x.flags)
    last_studio = max(i for i, x in enumerate(ordered) if "live" not in x.flags)
    assert first_live > last_studio


# --- collection ordering --------------------------------------------------


def test_album_order_groups_by_artist_then_album_then_track_number():
    rows = [
        t("Wilco", "Summerteeth", "Via Chicago", 5),
        t("Wilco", "Being There", "Misunderstood", 1),
        t("Wilco", "Being There", "Far Far Away", 2),
        t("Uncle Tupelo", "No Depression", "Graveyard Shift", 1),
    ]
    ordered = album_order(rows)
    assert [x.title for x in ordered] == [
        "Graveyard Shift",
        "Misunderstood",
        "Far Far Away",
        "Via Chicago",
    ]


def test_album_order_keeps_albums_contiguous():
    rows = [
        t("A", "Two", "b", 1),
        t("A", "One", "a", 1),
        t("A", "Two", "c", 2),
        t("A", "One", "d", 2),
    ]
    albums = [x.album for x in album_order(rows)]
    assert albums == ["One", "One", "Two", "Two"]


def test_missing_track_numbers_sort_last_within_an_album():
    rows = [t("A", "One", "zzz", None), t("A", "One", "aaa", 1)]
    assert [x.title for x in album_order(rows)] == ["aaa", "zzz"]


# --- strategy dispatch ----------------------------------------------------


def test_collection_strategy_uses_album_grouping():
    rows = [t("A", "One", "b", 2), t("A", "One", "a", 1)]
    definition = {"name": "C", "ordering": {"strategy": "album_grouped"}}
    assert [x.title for x in sequence_tracks(rows, definition)] == ["a", "b"]


def test_genre_strategy_uses_interleaving():
    rows = [t("A", "One", f"A{i}") for i in range(3)] + [
        t("B", "Two", f"B{i}") for i in range(3)
    ]
    definition = {"name": "G", "ordering": {"strategy": "interleaved", "seed": "g"}}
    ordered = sequence_tracks(rows, definition)
    assert all(ordered[i].artist != ordered[i + 1].artist for i in range(len(ordered) - 1))


def test_default_strategy_is_album_order():
    rows = [t("A", "One", "b", 2), t("A", "One", "a", 1)]
    assert [x.title for x in sequence_tracks(rows, {"name": "D"})] == ["a", "b"]


def test_deterministic_order_is_reproducible():
    rows = [t("A", "One", f"S{i}") for i in range(10)]
    assert [x.title for x in deterministic_order(rows, "s")] == [
        x.title for x in deterministic_order(list(reversed(rows)), "s")
    ]


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="Unsupported ordering strategy"):
        sequence_tracks([], {"name": "X", "ordering": {"strategy": "nonsense"}})


def test_every_known_strategy_dispatches():
    rows = [t("A", "One", "a", 1)]
    for strategy in KNOWN_STRATEGIES:
        assert len(sequence_tracks(rows, {"name": "X", "ordering": {"strategy": strategy}})) == 1


# --- same-song spacing ----------------------------------------------------


def test_duplicate_recordings_are_pushed_apart():
    """A studio take and a live take of one song should not sit adjacent."""
    rows = [
        make_track("A", "Studio", "Thunder Road"),
        make_track("A", "Live", "Thunder Road (Live at Leeds)", flags=("live",)),
        make_track("A", "Studio", "Badlands"),
        make_track("A", "Studio", "Jungleland"),
    ]
    ordered = space_duplicates(rows, min_gap=2)
    titles = [base_title(x.title) for x in ordered]
    first = titles.index("thunder road")
    second = len(titles) - 1 - titles[::-1].index("thunder road")
    assert second - first >= 2


def test_spacing_preserves_every_track():
    rows = [make_track("A", "Al", f"Song {i % 3}") for i in range(12)]
    assert len(space_duplicates(rows, min_gap=4)) == 12
    assert sorted(t.relative_path for t in space_duplicates(rows, min_gap=4)) == sorted(
        t.relative_path for t in rows
    )


def test_spacing_terminates_when_everything_is_the_same_song():
    rows = [make_track("A", f"Album {i}", "Same Song") for i in range(5)]
    assert len(space_duplicates(rows, min_gap=10)) == 5


def test_spacing_is_deterministic():
    rows = [make_track("A", "Al", f"Song {i % 4}") for i in range(16)]
    assert [t.relative_path for t in space_duplicates(rows, 3)] == [
        t.relative_path for t in space_duplicates(rows, 3)
    ]


def test_spacing_is_a_no_op_when_disabled():
    rows = [make_track("A", "Al", "Same") for _ in range(3)]
    assert space_duplicates(rows, 0) == rows


def test_same_title_by_different_artists_is_not_spaced():
    """A cover and the original are different recordings by different artists."""
    rows = [make_track("A", "Al", "Hurt"), make_track("B", "Al", "Hurt")]
    assert [t.artist for t in space_duplicates(rows, min_gap=5)] == ["A", "B"]


def test_album_ordering_is_never_spaced():
    """Collections keep album sequence even when titles repeat."""
    rows = [
        make_track("A", "Al", "Reprise", track_no=1),
        make_track("A", "Al", "Reprise", track_no=2),
    ]
    definition = {"name": "C", "ordering": {"strategy": "album_grouped"}}
    assert len(sequence_tracks(rows, definition)) == 2


def test_genre_sequencing_applies_spacing_by_default():
    rows = [make_track("A", "Al", "Echo") for _ in range(2)] + [
        make_track(f"B{i}", "Al", f"Other {i}") for i in range(30)
    ]
    definition = {"name": "G", "ordering": {"strategy": "interleaved", "seed": "s"}}
    ordered = sequence_tracks(rows, definition)
    positions = [i for i, t in enumerate(ordered) if base_title(t.title) == "echo"]
    assert positions[1] - positions[0] >= 2


def _longest_runs(order) -> int:
    longest = run = 0
    previous = None
    for track in order:
        run = run + 1 if track.artist == previous else 1
        previous = track.artist
        longest = max(longest, run)
    return longest


def test_max_consecutive_artist_is_a_maximum_run():
    """Regression: the setting acted as a minimum gap between an artist's tracks."""
    rows = [t(artist, f"{artist} LP", f"{artist} {i}") for artist in "ABC" for i in range(6)]
    order = interleaved_order(rows, "seed", max_consecutive_artist=2)
    assert len(order) == len(rows)
    assert _longest_runs(order) == 2


def test_max_consecutive_artist_of_one_never_repeats_an_artist():
    rows = [t(artist, f"{artist} LP", f"{artist} {i}") for artist in "ABC" for i in range(6)]
    assert _longest_runs(interleaved_order(rows, "seed", max_consecutive_artist=1)) == 1


def test_album_order_sorts_by_disc_before_track():
    rows = [
        make_track("A", "Box", "Disc Two Opener", track_no=1, disc_no=2),
        make_track("A", "Box", "Disc One Closer", track_no=9, disc_no=1),
        make_track("A", "Box", "No Disc Prefix", track_no=5),
    ]
    assert [x.title for x in album_order(rows)] == [
        "No Disc Prefix",
        "Disc One Closer",
        "Disc Two Opener",
    ]
