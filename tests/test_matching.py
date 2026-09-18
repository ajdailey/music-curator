"""Rockbox path matching, taxonomy matching, and identity-aware membership."""

from __future__ import annotations

import pytest

from music_engine.core import (
    PlaylistMatcher,
    artist_profiles,
    canonical_tags,
    normalize_relative_path,
    playlist_path_keys,
    safe_name,
    write_m3u,
)
from music_engine.identity import IdentityResolver
from tests.conftest import make_track

# --- Rockbox path matching ------------------------------------------------


def write_sync(tmp_path, lines: list[str], name: str = "iPod Sync.m3u8"):
    path = tmp_path / name
    path.write_text("\ufeff#EXTM3U\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


WINDOWS_ROOT = "M:\\itunes_portable\\iTunes\\iTunes Media\\Music\\"


def test_path_keys_strip_the_windows_root(tmp_path):
    path = write_sync(tmp_path, [WINDOWS_ROOT + "R.E.M_\\Murmur\\01 Radio.m4a"])
    assert playlist_path_keys(path, WINDOWS_ROOT) == {"r.e.m_/murmur/01 radio.m4a"}


def test_path_matching_is_case_and_separator_insensitive(tmp_path):
    path = write_sync(tmp_path, ["m:/ITUNES_PORTABLE/iTunes/iTunes Media/Music/A/B/C.m4a"])
    keys = playlist_path_keys(path, WINDOWS_ROOT)
    assert normalize_relative_path("A\\B\\C.m4a") in keys


def test_path_keys_ignore_comments_and_blank_lines(tmp_path):
    path = write_sync(
        tmp_path,
        ["#EXTINF:211,Artist - Song", "", WINDOWS_ROOT + "A\\B\\C.m4a", "#PLAYLIST:x"],
    )
    assert playlist_path_keys(path, WINDOWS_ROOT) == {"a/b/c.m4a"}


def test_path_keys_skip_absolute_paths_from_other_mounts(tmp_path):
    path = write_sync(tmp_path, ["Z:\\elsewhere\\A\\B.m4a", WINDOWS_ROOT + "A\\B.m4a"])
    assert playlist_path_keys(path, WINDOWS_ROOT) == {"a/b.m4a"}


def test_missing_sync_playlist_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        playlist_path_keys(tmp_path / "absent.m3u8", WINDOWS_ROOT)


def test_track_key_matches_sync_key_for_commas_and_apostrophes(tmp_path):
    """Real catalog paths contain commas and apostrophes (10,000 Maniacs)."""
    track = make_track("10,000 Maniacs", "Blind Man's Zoo", "Eat For Two")
    path = write_sync(tmp_path, [WINDOWS_ROOT + track.relative_path.replace("/", "\\")])
    assert track.key in playlist_path_keys(path, WINDOWS_ROOT)


# --- taxonomy matching ----------------------------------------------------


def build(tag_config, artists: dict, identities: dict | None = None):
    resolver = IdentityResolver.from_config({"identities": identities or {}})
    profiles = artist_profiles({"artists": artists}, tag_config, resolver)
    return resolver, profiles


def test_tag_closure_includes_ancestors(tag_config):
    closure, _ = canonical_tags(tag_config)
    assert closure["college-rock"] == {"college-rock", "rock", "music"}


def test_tag_alias_resolves_to_canonical_tag(tag_config):
    _, aliases = canonical_tags(tag_config)
    assert aliases["alternative"] == "college-rock"


def test_big_band_is_not_a_child_of_swing(tag_config):
    """Ensemble format and era are separate concepts."""
    closure, _ = canonical_tags(tag_config)
    assert "swing" not in closure["big-band"]
    assert "big-band" not in closure["swing"]


def test_cycle_in_taxonomy_is_rejected():
    config = {"tags": {"a": {"parents": ["b"]}, "b": {"parents": ["a"]}}}
    with pytest.raises(ValueError, match="cycle"):
        canonical_tags(config)


def test_profile_written_with_correct_spelling_matches_catalog_spelling(tag_config):
    """The R.E.M. defect, end to end.

    The profile is written 'R.E.M.'; the catalog stores 'R.E.M_'. Membership
    must still resolve.
    """
    resolver, profiles = build(
        tag_config,
        {"R.E.M.": {"tags": ["college-rock"]}},
        identities={"R.E.M.": {"aliases": ["R.E.M_"]}},
    )
    matcher = PlaylistMatcher(
        {"name": "College Rock", "match_any_tags": ["college-rock"]}, profiles, resolver
    )
    assert matcher.select([make_track("R.E.M_", "Murmur", "Radio")])


def test_collection_include_artists_matches_catalog_spelling(tag_config):
    resolver, profiles = build(
        tag_config, {}, identities={"R.E.M.": {"aliases": ["R.E.M_"]}}
    )
    matcher = PlaylistMatcher(
        {"name": "R.E.M. Collection", "include_artists": ["R.E.M."]}, profiles, resolver
    )
    assert matcher.select([make_track("R.E.M_", "Murmur", "Radio")])


def test_related_identities_require_explicit_opt_in(tag_config):
    identities = {"Neil Young": {"related": ["Crazy Horse"]}}
    resolver, profiles = build(tag_config, {}, identities=identities)
    tracks = [
        make_track("Neil Young", "Harvest", "S"),
        make_track("Crazy Horse", "Crazy Horse", "S"),
    ]

    without = PlaylistMatcher(
        {"name": "NY", "include_artists": ["Neil Young"]}, profiles, resolver
    )
    with_related = PlaylistMatcher(
        {"name": "NY", "include_artists": ["Neil Young"], "include_related": True},
        profiles,
        resolver,
    )
    assert len(without.select(tracks)) == 1
    assert len(with_related.select(tracks)) == 2


def test_match_all_tags_requires_every_tag(tag_config):
    resolver, profiles = build(
        tag_config, {"A": {"tags": ["jazz"]}, "B": {"tags": ["jazz", "swing"]}}
    )
    matcher = PlaylistMatcher(
        {"name": "Swing", "match_all_tags": ["jazz", "swing"]}, profiles, resolver
    )
    selected = matcher.select([make_track("A", "x", "y"), make_track("B", "x", "y")])
    assert {t.artist for t in selected} == {"B"}


def test_exclude_artists_wins_over_tag_match(tag_config):
    resolver, profiles = build(tag_config, {"A": {"tags": ["rock"]}})
    matcher = PlaylistMatcher(
        {"name": "R", "match_any_tags": ["rock"], "exclude_artists": ["A"]},
        profiles,
        resolver,
    )
    assert matcher.select([make_track("A", "x", "y")]) == []


def test_exclude_flags_filter_individual_tracks(tag_config):
    resolver, profiles = build(tag_config, {"A": {"tags": ["rock"]}})
    matcher = PlaylistMatcher(
        {"name": "R", "match_any_tags": ["rock"], "exclude_flags": ["live"]},
        profiles,
        resolver,
    )
    tracks = [make_track("A", "x", "y"), make_track("A", "x", "z", flags=("live",))]
    assert len(matcher.select(tracks)) == 1


def test_unquoted_yaml_artist_name_is_rejected(tag_config):
    with pytest.raises(ValueError, match="not a string"):
        artist_profiles({"artists": {True: {"tags": ["rock"]}}}, tag_config)


# --- output ---------------------------------------------------------------


def test_empty_playlist_is_not_written(tmp_path):
    path = tmp_path / "Empty.m3u8"
    assert write_m3u(path, [], "/music/") == 0
    assert not path.exists()


def test_empty_playlist_removes_a_stale_file(tmp_path):
    path = tmp_path / "Stale.m3u8"
    path.write_text("#EXTM3U\nold\n", encoding="utf-8")
    write_m3u(path, [], "/music/")
    assert not path.exists()


def test_written_playlist_uses_the_configured_root(tmp_path):
    path = tmp_path / "P.m3u8"
    write_m3u(path, [make_track("A", "B", "C")], "../Music/")
    body = path.read_text(encoding="utf-8")
    assert "../Music/A/B/C.m4a" in body
    assert body.startswith("#EXTM3U\n")


def test_safe_name_strips_filesystem_reserved_characters():
    result = safe_name("AC/DC: Live?")
    assert not set(result) & set('<>:"/\\|?*')
    assert result.startswith("AC - DC")
    # Names already free of reserved characters must pass through untouched, or
    # existing output files would be orphaned.
    assert safe_name("R.E.M. Collection") == "R.E.M. Collection"
    assert safe_name("90s Alternative") == "90s Alternative"


def test_playlist_tag_alias_selects_the_canonical_tag(tag_config):
    """Regression: validate resolved tag aliases but the matcher did not."""
    profiles = artist_profiles(
        {"artists": {"R.E.M.": {"tags": ["college-rock"]}}}, tag_config
    )
    _, aliases = canonical_tags(tag_config)
    resolver = IdentityResolver()
    track = make_track("R.E.M.", "Murmur", "Talk About The Passion")
    for key in ("match_any_tags", "match_all_tags"):
        definition = {"name": "Alt", key: ["Alternative"]}
        matcher = PlaylistMatcher(definition, profiles, resolver, tag_aliases=aliases)
        assert matcher.select([track]) == [track], key
