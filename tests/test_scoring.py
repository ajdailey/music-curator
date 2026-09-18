"""Song prominence scoring."""

from __future__ import annotations

from music_engine.popularity import PopularityData
from music_engine.scoring import (
    UNMEASURED_CEILING,
    build_popularity,
    prominence_report,
)
from tests.conftest import make_track


def catalog_for(artist: str, spec: dict[str, list[str]]):
    """spec: album -> titles"""
    return [
        make_track(artist, album, title)
        for album, titles in spec.items()
        for title in titles
    ]


def test_a_re_released_song_outranks_a_deep_cut():
    """Hits get re-released; album tracks do not."""
    tracks = catalog_for(
        "STP",
        {
            "Purple": ["Interstate Love Song", "Meatplow"],
            "Purple (Remaster)": ["Interstate Love Song", "Meatplow"],
            "Greatest Hits": ["Interstate Love Song"],
            "Live 1994": ["Interstate Love Song"],
        },
    )
    index = build_popularity(tracks)
    assert index.score("STP", "Interstate Love Song") > index.score("STP", "Meatplow")


def test_scores_are_normalized_within_each_artist():
    """A prolific artist's deep cut must not outrank a small artist's hit."""
    big = catalog_for(
        "Big", {f"Album {i}": ["Deep Cut"] for i in range(5)} | {"Hits": ["Big Hit"] * 1}
    )
    small = catalog_for("Small", {"Only Album": ["Their Best Song"]})
    index = build_popularity(big + small)
    assert index.score("Small", "Their Best Song") == UNMEASURED_CEILING
    assert index.score("Big", "Deep Cut") <= UNMEASURED_CEILING


def test_unmeasured_scores_are_capped_below_measured_ones():
    """An inferred score must never outrank a song with real listener data."""
    tracks = catalog_for("A", {"One": ["X", "Y"], "Two": ["X"]})
    index = build_popularity(tracks)
    assert index.score("A", "X") == UNMEASURED_CEILING
    assert index.score("A", "X") > index.score("A", "Y")
    assert not index.measured


def test_measured_listener_data_takes_precedence():
    tracks = catalog_for("STP", {"Purple": ["Interstate Love Song", "Meatplow"]})
    # The heuristic cannot separate these: both appear on exactly one album.
    assert build_popularity(tracks).score("STP", "Interstate Love Song") == (
        build_popularity(tracks).score("STP", "Meatplow")
    )

    measured = PopularityData(
        songs={
            "stp|interstate love song": {"listeners": 704161},
            "stp|meatplow": {"listeners": 12000},
        }
    )
    index = build_popularity(tracks, measured=measured)
    assert index.score("STP", "Interstate Love Song") == 1.0
    assert index.score("STP", "Meatplow") < 0.1
    assert index.measured_coverage == 1.0


def test_a_measured_song_outranks_an_unmeasured_one():
    tracks = catalog_for("A", {f"Album {i}": ["Reissued Filler"] for i in range(8)})
    tracks += catalog_for("A", {"Obscure": ["Real Hit"]})
    measured = PopularityData(songs={"a|real hit": {"listeners": 500000}})
    index = build_popularity(tracks, measured=measured)
    assert index.score("A", "Real Hit") > index.score("A", "Reissued Filler")


def test_compilation_appearances_count_double():
    plain = catalog_for("A", {"Album": ["Song"], "Another": ["Song"]})
    comp = catalog_for("A", {"Album": ["Song"], "Greatest Hits": ["Song"]})
    assert build_popularity(comp).scores[("a", "song")] >= (
        build_popularity(plain).scores[("a", "song")]
    )


def test_duplicate_copies_on_one_album_count_once():
    once = catalog_for("A", {"Album": ["Song"], "Other": ["Filler"]})
    twice = [
        make_track("A", "Album", "Song"),
        make_track("A", "Album", "Song (Remastered)"),
        make_track("A", "Other", "Filler"),
    ]
    assert build_popularity(once).appearances[("a", "song")] == (
        build_popularity(twice).appearances[("a", "song")]
    )


def test_version_markers_do_not_split_a_song():
    tracks = [
        make_track("A", "Album", "Song"),
        make_track("A", "Live", "Song (Live at Leeds)"),
        make_track("A", "Hits", "Song - Remastered 2011"),
    ]
    index = build_popularity(tracks)
    assert index.appearances[("a", "song")] == 3


def test_curated_essential_overrides_the_heuristic():
    tracks = catalog_for("A", {f"Album {i}": ["Popular"] for i in range(6)})
    tracks += catalog_for("A", {"Obscure": ["Overlooked Gem"]})
    curated = {"A": {"essential": ["Overlooked Gem"]}}
    index = build_popularity(tracks, curated=curated)
    assert index.score("A", "Overlooked Gem") == 1.0


def test_curated_demote_overrides_the_heuristic():
    tracks = catalog_for("A", {f"Album {i}": ["Overplayed"] for i in range(6)})
    index = build_popularity(tracks, curated={"A": {"demote": ["Overplayed"]}})
    assert index.score("A", "Overplayed") == 0.0


def test_scoring_groups_alias_spellings():
    tracks = [
        make_track("R.E.M.", "Murmur", "Radio Free Europe"),
        make_track("R.E.M_", "Eponymous", "Radio Free Europe"),
    ]
    index = build_popularity(
        tracks, identity_of={"R.E.M.": "R.E.M.", "R.E.M_": "R.E.M."}
    )
    assert index.appearances[("r.e.m.", "radio free europe")] == 2


def test_unknown_song_scores_zero():
    index = build_popularity([])
    assert index.score("Nobody", "Nothing") == 0.0


def test_prominence_report_lists_top_songs():
    tracks = catalog_for("A", {"One": ["X", "Y"], "Two": ["X"]})
    rows = prominence_report(build_popularity(tracks), ["A"], limit=2)
    assert rows[0]["artist"] == "A"
    assert rows[0]["top_songs"][0]["title"] == "x"


def test_curated_lists_resolve_the_profile_artist_through_identities():
    """Regression: a profile key absent from the catalog spellings (``REM`` for
    catalog ``R.E.M_``) was looked up raw and its lists applied to nothing."""
    from music_engine.identity import IdentityResolver

    resolver = IdentityResolver.from_config({"identities": {"R.E.M.": {"aliases": ["REM"]}}})
    tracks = [make_track("R.E.M_", f"Album {i}", "Popular") for i in range(6)]
    tracks += [make_track("R.E.M_", "Obscure", "Overlooked Gem")]
    identity_of = {"R.E.M_": resolver.resolve("R.E.M_")}
    index = build_popularity(
        tracks,
        identity_of,
        curated={"REM": {"essential": ["Overlooked Gem"]}},
        resolve_name=resolver.resolve,
    )
    assert index.score("R.E.M.", "Overlooked Gem") == 1.0
