"""Candidate balancing: soft caps, cornerstones, live quotas, determinism."""

from __future__ import annotations

from music_engine.balancing import BalanceConfig, balance_candidates, solve_share_cap
from tests.conftest import make_track


def catalog(spec: dict[str, int], flags: tuple[str, ...] = ()) -> list:
    return [
        make_track(artist, f"{artist} Album", f"{artist} Song {i}", flags=flags)
        for artist, count in spec.items()
        for i in range(count)
    ]


# --- the share-cap solver -------------------------------------------------


def test_share_cap_is_measured_against_the_candidate_pool():
    available = [900, 800, 600, 400, 300, 150, 98]  # 3248 total
    assert solve_share_cap(available, 0.10) == 325


def test_share_cap_does_not_collapse_a_playlist_with_few_artists():
    """Regression: Outlaw Country collapsed from 3260 tracks to 168.

    Four artists, the smallest holding 42 tracks. Constraining the finished
    playlist dragged every artist down to 42. Measuring against the pool keeps
    the playlist intact while still trimming the dominant artist.
    """
    available = [1817, 1045, 356, 42]  # Cash, Haggard, Nelson, Jennings
    cap = solve_share_cap(available, 0.25)
    kept = sum(min(c, cap) for c in available)
    assert cap == 815
    assert kept == 2028  # not 168
    assert max(min(c, cap) for c in available) < 1817  # Cash is still trimmed


def test_share_cap_does_not_bind_when_no_artist_exceeds_it():
    # 20 artists with 100 tracks each; 10% of 2000 is 200, above every artist.
    assert solve_share_cap([100] * 20, 0.10) == 200


def test_share_cap_trims_a_single_dominant_artist():
    available = [1000] + [50] * 30
    cap = solve_share_cap(available, 0.10)
    kept = sum(min(c, cap) for c in available)
    assert cap == 250
    assert kept == 1750
    # Domination drops from 40% of the pool to 14% of the playlist.
    assert 250 / kept < 0.15


def test_share_cap_is_monotone_in_share():
    available = [1817, 1045, 356, 42]
    caps = [solve_share_cap(available, s) for s in (0.10, 0.25, 0.35, 0.50)]
    assert caps == sorted(caps)


def test_share_cap_ignores_uncapped_configuration():
    assert solve_share_cap([10, 20], 0) == 20


# --- balancing behavior ---------------------------------------------------


def test_balancing_disabled_is_a_passthrough():
    tracks = catalog({"A": 50, "B": 5})
    config = BalanceConfig(enabled=False)
    result, report = balance_candidates(tracks, config)
    assert len(result) == len(tracks)
    assert report.removed_artist_cap == 0


def test_collections_preserve_every_track():
    """Artist collections must not be balanced (Musical Constitution #1)."""
    tracks = catalog({"Neil Young": 200})
    definition = {"name": "Neil Young Collection", "type": "collection"}
    config = BalanceConfig.from_definition(definition, {"enabled": False})
    result, _ = balance_candidates(tracks, config)
    assert len(result) == 200


def test_artist_soft_cap_limits_domination():
    tracks = catalog({"Hog": 500, **{f"Artist{i}": 40 for i in range(20)}})
    config = BalanceConfig(
        enabled=True, max_share_per_artist=0.10, dedupe_versions=False
    )
    result, report = balance_candidates(tracks, config)
    hog = sum(1 for t in result if t.artist == "Hog")
    assert hog < 500
    # The cap is 10% of the 1300-track pool. The dominant artist falls from
    # 38% of the pool to 14% of the playlist; the share bounds the pool, not
    # the finished playlist.
    assert hog == 130
    assert hog / len(result) < 0.15
    assert report.capped_artists["Hog"]["available"] == 500


def test_cornerstone_artists_receive_a_higher_cap():
    spec = {"Star": 500, **{f"Artist{i}": 40 for i in range(20)}}
    config = BalanceConfig(
        enabled=True,
        max_share_per_artist=0.10,
        cornerstone_multiplier=2.0,
        dedupe_versions=False,
    )
    plain, _ = balance_candidates(catalog(spec), config)
    boosted, _ = balance_candidates(catalog(spec), config, cornerstones={"Star"})
    assert sum(1 for t in boosted if t.artist == "Star") > sum(
        1 for t in plain if t.artist == "Star"
    )


def test_hard_track_cap_is_respected():
    tracks = catalog({"A": 100, "B": 100})
    config = BalanceConfig(enabled=True, max_tracks_per_artist=10, dedupe_versions=False)
    result, _ = balance_candidates(tracks, config)
    assert sum(1 for t in result if t.artist == "A") == 10
    assert sum(1 for t in result if t.artist == "B") == 10


def test_min_tracks_per_artist_is_a_floor():
    tracks = catalog({"A": 100, "B": 1})
    config = BalanceConfig(
        enabled=True,
        max_tracks_per_artist=5,
        min_tracks_per_artist=3,
        dedupe_versions=False,
    )
    result, _ = balance_candidates(tracks, config)
    assert sum(1 for t in result if t.artist == "B") == 1  # keeps all it has


def test_live_quota_limits_live_share():
    live = [
        make_track("A", "Live Album", f"Live Song {i}", flags=("live",))
        for i in range(80)
    ]
    studio = [make_track("A", "Studio Album", f"Studio Song {i}") for i in range(20)]
    config = BalanceConfig(enabled=True, max_live_share=0.25, dedupe_versions=False)
    result, report = balance_candidates(live + studio, config)
    live_count = sum(1 for t in result if "live" in t.flags)
    assert live_count / len(result) <= 0.25
    assert report.removed_live_quota > 0
    assert report.live_share_before > report.live_share_after


def test_zero_live_quota_removes_all_live_tracks():
    tracks = [
        make_track("A", "Live", f"Song {i}", flags=("live",)) for i in range(10)
    ] + [make_track("A", "Studio", f"Other {i}") for i in range(10)]
    config = BalanceConfig(enabled=True, max_live_share=0.0, dedupe_versions=False)
    result, _ = balance_candidates(tracks, config)
    assert not any("live" in t.flags for t in result)


def test_remix_quota_caps_remixes_in_the_finished_playlist():
    remixes = [
        make_track("A", "The Remixes", f"Song {i} (Club Mix)") for i in range(80)
    ]
    studio = [make_track("A", "Studio Album", f"Track {i}") for i in range(20)]
    config = BalanceConfig(enabled=True, max_remix_share=0.05, dedupe_versions=False)
    result, report = balance_candidates(remixes + studio, config)
    remix_count = sum(1 for t in result if "remix" in t.title.lower() or "mix" in t.album.lower())
    assert remix_count / len(result) <= 0.05
    assert report.removed_remix_quota > 0
    assert report.remix_share_before > report.remix_share_after


def test_zero_remix_quota_removes_every_remix():
    tracks = [
        make_track("A", "The Remixes", f"Song {i} (Club Mix)") for i in range(10)
    ] + [make_track("A", "Studio", f"Other {i}") for i in range(10)]
    config = BalanceConfig(enabled=True, max_remix_share=0.0, dedupe_versions=False)
    result, _ = balance_candidates(tracks, config)
    assert len(result) == 10
    assert all(t.album == "Studio" for t in result)


def test_remix_quota_is_not_applied_when_unset():
    tracks = [
        make_track("A", "The Remixes", f"Song {i} (Club Mix)") for i in range(10)
    ] + [make_track("A", "Studio", f"Other {i}") for i in range(10)]
    config = BalanceConfig(enabled=True, dedupe_versions=False)
    result, report = balance_candidates(tracks, config)
    assert len(result) == 20
    assert report.removed_remix_quota == 0


def test_version_collapsing_drops_a_remix_before_the_quota_is_reached():
    """The quota is a backstop. Collapsing is what removes most remixes."""
    tracks = [
        make_track("A", "Album", "Song"),
        make_track("A", "The Remixes", "Song (Club Mix)"),
    ]
    config = BalanceConfig(enabled=True, dedupe_versions=True, max_remix_share=0.5)
    result, report = balance_candidates(tracks, config)
    assert [t.title for t in result] == ["Song"]
    assert report.removed_duplicates == 1
    assert report.removed_remix_quota == 0


def test_balancing_is_deterministic():
    tracks = catalog({"A": 60, "B": 40, "C": 30})
    config = BalanceConfig(enabled=True, max_share_per_artist=0.4, seed="fixed")
    first, _ = balance_candidates(tracks, config)
    second, _ = balance_candidates(list(reversed(tracks)), config)
    assert [t.relative_path for t in first] == [t.relative_path for t in second]


def test_balancing_prefers_studio_over_live_when_capping():
    tracks = [
        make_track("A", "Live Album", f"Song {i}", flags=("live",)) for i in range(50)
    ] + [make_track("A", "Studio Album", f"Track {i}") for i in range(10)]
    config = BalanceConfig(
        enabled=True, max_tracks_per_artist=10, dedupe_versions=False, prefer_studio=True
    )
    result, _ = balance_candidates(tracks, config)
    assert all("live" not in t.flags for t in result)


def test_balance_report_accounts_for_every_removal():
    tracks = catalog({"A": 100, "B": 20})
    config = BalanceConfig(
        enabled=True, max_tracks_per_artist=10, dedupe_versions=False
    )
    result, report = balance_candidates(tracks, config)
    assert report.input_tracks == 120
    assert report.output_tracks == len(result)
    assert (
        report.input_tracks
        - report.removed_duplicates
        - report.removed_artist_cap
        - report.removed_live_quota
        == report.output_tracks
    )


def test_share_cap_does_not_trim_a_single_artist_playlist():
    """Regression: a single-artist genre playlist of 400 kept 100 at share 0.25."""
    assert solve_share_cap([400], 0.25) == 400
    tracks = catalog({"A": 400})
    config = BalanceConfig(enabled=True, max_share_per_artist=0.25, dedupe_versions=False)
    kept, report = balance_candidates(tracks, config)
    assert len(kept) == 400
    assert report.removed_artist_cap == 0


def test_share_cap_does_not_trim_equal_artists_below_one_over_share():
    # Three equal artists cannot get below 33% each; a 0.25 cap only drops tracks.
    assert solve_share_cap([100, 100, 100], 0.25) == 100


def test_share_cap_still_trims_a_dominant_artist_among_few():
    cap = solve_share_cap([300, 100, 100], 0.25)
    assert cap == 125


def test_remix_and_live_quotas_both_hold_on_the_final_playlist():
    """Regression: trimming live after remix pushed the remix share over its limit."""
    tracks = (
        [make_track("A", "Studio", f"S{i}") for i in range(6)]
        + [make_track("B", "Remixes", f"R{i}", flags=("remix",)) for i in range(5)]
        + [make_track("C", "Live", f"L{i}", flags=("live",)) for i in range(9)]
    )
    config = BalanceConfig(
        enabled=True, max_remix_share=0.25, max_live_share=0.25, dedupe_versions=False
    )
    kept, report = balance_candidates(tracks, config)
    assert report.remix_share_after <= 0.25
    assert report.live_share_after <= 0.25
    assert len(kept) == 12
    assert report.removed_remix_quota == 2
    assert report.removed_live_quota == 6
