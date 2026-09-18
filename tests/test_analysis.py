"""Overlap, domination, coverage, and zero-count explanation."""

from __future__ import annotations

from music_engine.analysis import coverage, domination, explain_zero, overlap, overlap_matrix
from tests.conftest import make_track


def album(artist: str, count: int, prefix: str = "S"):
    return [make_track(artist, f"{artist} Album", f"{prefix}{i}") for i in range(count)]


# --- overlap --------------------------------------------------------------


def test_identical_playlists_have_jaccard_one():
    tracks = album("A", 5)
    stats = overlap("X", tracks, "Y", list(tracks))
    assert stats.track_jaccard == 1.0
    assert stats.shared_tracks == 5
    assert stats.left_only_tracks == 0
    assert stats.is_effective_duplicate


def test_disjoint_playlists_have_jaccard_zero():
    stats = overlap("X", album("A", 3), "Y", album("B", 3))
    assert stats.track_jaccard == 0.0
    assert stats.shared_artists == 0
    assert not stats.is_effective_duplicate


def test_partial_overlap_is_measured_both_ways():
    left = album("A", 4)
    right = left[:2] + album("B", 2)
    stats = overlap("X", left, "Y", right)
    assert stats.shared_tracks == 2
    assert stats.left_only_tracks == 2
    assert stats.right_only_tracks == 2
    assert stats.track_jaccard == 2 / 6
    assert stats.overlap_of_smaller == 0.5


def test_subset_playlist_fully_overlaps_the_smaller_side():
    big = album("A", 10)
    stats = overlap("Big", big, "Small", big[:3])
    assert stats.overlap_of_smaller == 1.0
    assert stats.track_jaccard == 0.3


def test_overlap_counts_artists_by_identity():
    left = [make_track("R.E.M.", "Murmur", "S1")]
    right = [make_track("R.E.M_", "Murmur", "S2")]
    stats = overlap("X", left, "Y", right, identity_of={"R.E.M_": "R.E.M.", "R.E.M.": "R.E.M."})
    assert stats.shared_artists == 1
    assert stats.artist_jaccard == 1.0


def test_overlap_matrix_skips_unknown_playlist_names():
    playlists = {"A": album("A", 2)}
    assert overlap_matrix(playlists, [("A", "Missing")]) == []


def test_empty_playlists_do_not_divide_by_zero():
    stats = overlap("X", [], "Y", [])
    assert stats.track_jaccard == 0.0
    assert stats.overlap_of_smaller == 0.0


# --- domination -----------------------------------------------------------


def test_even_split_has_full_effective_diversity():
    tracks = album("A", 10) + album("B", 10) + album("C", 10) + album("D", 10)
    stats = domination("Even", tracks)
    assert stats.distinct_artists == 4
    assert round(stats.effective_artists, 2) == 4.0
    assert stats.top_artist_share == 0.25


def test_dominated_playlist_has_low_effective_diversity():
    tracks = album("Hog", 90) + album("B", 5) + album("C", 5)
    stats = domination("Dominated", tracks)
    assert stats.top_artist == "Hog"
    assert stats.top_artist_share == 0.9
    assert stats.effective_artists < 1.5
    assert stats.is_dominated


def test_domination_groups_alias_spellings_into_one_artist():
    tracks = [make_track("R.E.M.", "A", "S1"), make_track("R.E.M_", "B", "S2")]
    stats = domination("X", tracks, identity_of={"R.E.M.": "R.E.M.", "R.E.M_": "R.E.M."})
    assert stats.distinct_artists == 1


def test_domination_of_empty_playlist_is_safe():
    stats = domination("Empty", [])
    assert stats.tracks == 0
    assert stats.top_artist is None


# --- coverage and zero explanation ---------------------------------------


def test_coverage_reports_absent_artists():
    plex = album("A", 10) + album("B", 10)
    rockbox = album("A", 4)
    result = coverage("P", plex, rockbox)
    assert result["coverage_pct"] == 20.0
    assert result["artists_absent_from_portable"] == 1
    assert result["top_absent_artists"][0]["artist"] == "B"


def test_zero_is_explained_as_genuine_absence():
    """Neil Young is in the catalog but not on the iPod."""
    plex = album("Neil Young", 100)
    result = explain_zero("Neil Young Collection", "rockbox", plex, album("Other", 5), [])
    assert result["reason"] == "absent_from_source"
    assert result["matching_artists_in_source"] == 0
    assert result["example_artists"][0]["artist"] == "Neil Young"


def test_zero_is_explained_as_a_defect_when_the_artist_is_present():
    plex = album("Neil Young", 100)
    source = album("Neil Young", 100)
    result = explain_zero("Neil Young Collection", "rockbox", plex, source, [])
    assert result["reason"] == "removed_downstream"


def test_zero_is_explained_as_unpopulated_taxonomy():
    result = explain_zero("Nothing", "plex", [], album("A", 5), [])
    assert result["reason"] == "no_catalog_matches"


def test_non_empty_playlist_is_not_reported_as_zero():
    tracks = album("A", 3)
    result = explain_zero("P", "plex", tracks, tracks, tracks)
    assert result["reason"] == "not_zero"
