"""Album-level knowledge: pattern rules, explicit entries, and application."""

from __future__ import annotations

import pytest

from music_engine.albums import AlbumProfiles, base_album
from music_engine.versions import classify
from tests.conftest import make_track

BOOTLEG = {
    "patterns": [
        {
            "name": "dated-concert-recording",
            "album_matches": r"^(19|20)\d{2}[-_./]\d{2}[-_./]\d{2}(\s|$)",
            "flags": ["live"],
        }
    ]
}


def test_pattern_flags_a_dated_concert_album():
    """Regression: 2,242 bootleg tracks were classified as studio takes."""
    profiles = AlbumProfiles.from_config(BOOTLEG)
    assert profiles.flags_for("Bruce Springsteen", "1975_10_18 West Hollywood, CA") == {
        "live"
    }


def test_pattern_accepts_common_date_separators():
    profiles = AlbumProfiles.from_config(BOOTLEG)
    for album in ("2003-07-08 Boston, MA", "1998.04.12 Seattle, WA", "1975_10_18 X"):
        assert "live" in profiles.flags_for("A", album)


def test_pattern_does_not_flag_a_studio_album():
    profiles = AlbumProfiles.from_config(BOOTLEG)
    assert profiles.flags_for("Bruce Springsteen", "Nebraska") == frozenset()


def test_pattern_does_not_flag_an_album_merely_starting_with_a_year():
    """'1984' and '1989' are studio albums, not dated concert recordings."""
    profiles = AlbumProfiles.from_config(BOOTLEG)
    assert profiles.flags_for("Van Halen", "1984") == frozenset()
    assert profiles.flags_for("Thelonious Monk", "1947-1948") == frozenset()


def test_applied_flags_make_the_track_classify_as_live():
    profiles = AlbumProfiles.from_config(BOOTLEG)
    track = make_track("Bruce Springsteen", "1975_10_18 West Hollywood, CA", "Thunder Road")
    assert classify(track).is_studio  # before album knowledge

    updated, changed = profiles.apply_flags([track])
    assert changed == 1
    assert classify(updated[0]).is_live


def test_apply_flags_leaves_unmatched_tracks_untouched():
    profiles = AlbumProfiles.from_config(BOOTLEG)
    track = make_track("Bruce Springsteen", "Nebraska", "Atlantic City")
    updated, changed = profiles.apply_flags([track])
    assert changed == 0
    assert updated[0] is track


def test_apply_flags_is_idempotent():
    profiles = AlbumProfiles.from_config(BOOTLEG)
    tracks = [make_track("A", "1975_10_18 Boston, MA", "Song")]
    once, first = profiles.apply_flags(tracks)
    twice, second = profiles.apply_flags(once)
    assert first == 1 and second == 0
    assert [t.flags for t in once] == [t.flags for t in twice]


# --- explicit entries -----------------------------------------------------

NEBRASKA = {
    "albums": {
        "Bruce Springsteen": {
            "Nebraska": {
                "tags": ["singer-songwriter", "folk"],
                "masterpiece": True,
                "complete_album": True,
                "year": 1982,
            }
        }
    }
}


def test_explicit_album_contributes_tags_its_artist_lacks():
    """Nebraska is folk; Born in the U.S.A. is not."""
    profiles = AlbumProfiles.from_config(NEBRASKA)
    assert profiles.tags_for("Bruce Springsteen", "Nebraska") == {
        "singer-songwriter",
        "folk",
    }
    assert profiles.tags_for("Bruce Springsteen", "Born in the U.S.A.") == frozenset()


def test_album_lookup_is_case_insensitive():
    profiles = AlbumProfiles.from_config(NEBRASKA)
    assert "folk" in profiles.tags_for("bruce springsteen", "NEBRASKA")


def test_album_attributes_are_available():
    profiles = AlbumProfiles.from_config(NEBRASKA)
    entry = profiles.entry_for("Bruce Springsteen", "Nebraska")
    assert entry.masterpiece and entry.complete_album
    assert entry.year == 1982


def test_patterns_and_entries_combine():
    config = {**BOOTLEG, "albums": {"A": {"1975_10_18 Boston, MA": {"tags": ["rock"]}}}}
    profiles = AlbumProfiles.from_config(config)
    assert "live" in profiles.flags_for("A", "1975_10_18 Boston, MA")
    assert "rock" in profiles.tags_for("A", "1975_10_18 Boston, MA")


def test_pattern_without_expression_is_rejected():
    with pytest.raises(ValueError, match="album_matches"):
        AlbumProfiles.from_config({"patterns": [{"name": "bad"}]})


def test_unquoted_yaml_album_key_is_rejected():
    with pytest.raises(ValueError, match="not a string"):
        AlbumProfiles.from_config({"albums": {"A": {True: {"tags": ["rock"]}}}})


def test_empty_config_is_inert():
    profiles = AlbumProfiles.from_config({})
    track = make_track("A", "B", "C")
    updated, changed = profiles.apply_flags([track])
    assert changed == 0 and updated == [track]


def test_statistics_report_coverage_and_absent_albums():
    profiles = AlbumProfiles.from_config(NEBRASKA)
    stats = profiles.statistics([make_track("Bruce Springsteen", "Nebraska", "S")])
    assert stats["tracks_matched_by_explicit_entry"] == 1
    assert stats["declared_albums_absent_from_catalog"] == []

    stats = profiles.statistics([make_track("Other", "Other Album", "S")])
    assert stats["declared_albums_absent_from_catalog"] == [
        "Bruce Springsteen - Nebraska"
    ]


# --- edition and truncation tolerance -------------------------------------


def test_base_album_strips_edition_wrappers():
    assert base_album("Murmur - Deluxe Edition") == "murmur"
    assert base_album("Kind Of Blue (Legacy Edition)") == "kind of blue"
    assert base_album("Yankee Hotel Foxtrot (2022 Remaster)") == "yankee hotel foxtrot"
    assert base_album("Born In The U.S.A. (Remastered)") == "born in the u.s.a."


def test_base_album_repairs_the_40_character_truncation():
    """The export truncates every path segment at 40 characters."""
    assert base_album("Automatic For The People (25th Anniversa") == (
        "automatic for the people"
    )


def test_base_album_keeps_distinct_records_distinct():
    """Stripping wrappers must never merge different albums."""
    assert base_album("Harvest") != base_album("Harvest Moon")
    assert base_album("Nebraska") != base_album("Nebraska Outtakes")


def test_entry_matches_every_edition_of_a_record():
    profiles = AlbumProfiles.from_config(NEBRASKA)
    for edition in ("Nebraska", "Nebraska (24_192 Hi-Res)", "NEBRASKA (Remastered)"):
        assert "folk" in profiles.tags_for("Bruce Springsteen", edition)
    assert "folk" not in profiles.tags_for("Bruce Springsteen", "Nebraska Outtakes")


def test_entry_regex_captures_an_album_family():
    config = {
        "albums": {
            "Bruce Springsteen": {
                "Nebraska": {"album_matches": "(?i)^nebraska", "tags": ["folk"]}
            }
        }
    }
    profiles = AlbumProfiles.from_config(config)
    assert "folk" in profiles.tags_for("Bruce Springsteen", "Nebraska Outtakes")
    assert "folk" in profiles.tags_for("Bruce Springsteen", "Nebraska '82_ Expanded")


def test_clear_flags_keeps_live_through_this_a_studio_record():
    """Regression: the album title matched the live text and flag patterns."""
    from music_engine.catalog import derive_flags

    profiles = AlbumProfiles.from_config(
        {"albums": {"Hole": {"Live Through This": {"clear_flags": ["live"]}}}}
    )
    album = "Live Through This"
    track = make_track("Hole", album, "Violet", flags=tuple(derive_flags(album, "Violet")))
    assert classify(track).is_live
    (applied,), changed = profiles.apply_flags([track])
    assert changed == 1
    assert "live" not in applied.flags
    assert classify(applied).is_studio


def test_clear_tags_and_clear_flags_default_to_no_change():
    profiles = AlbumProfiles.from_config({"albums": {"Hole": {"Celebrity Skin": {}}}})
    assert profiles.cleared_tags_for("Hole", "Celebrity Skin") == frozenset()
    assert profiles.cleared_flags_for("Hole", "Celebrity Skin") == frozenset()
    track = make_track("Hole", "Celebrity Skin", "Malibu")
    assert profiles.apply_flags([track]) == ([track], 0)


def test_unknown_clear_names_are_errors():
    profiles = AlbumProfiles.from_config(
        {"albums": {"Hole": {"X": {"clear_tags": ["grunge", "nope"], "clear_flags": ["lve"]}}}}
    )
    errors = profiles.errors({"grunge"})
    assert any("undeclared tag 'nope'" in e for e in errors)
    assert any("unknown flag 'lve'" in e for e in errors)
    assert not any("'grunge'" in e for e in errors)


def test_catalog_flags_are_all_clearable():
    from music_engine.catalog import _FLAG_PATTERNS
    from music_engine.versions import KNOWN_FLAGS

    assert {name for name, _ in _FLAG_PATTERNS} <= KNOWN_FLAGS


def test_album_entry_artist_key_resolves_through_identities():
    """Regression: an entry written under an alias spelling never matched."""
    from music_engine.identity import IdentityResolver

    resolver = IdentityResolver.from_config({"identities": {"R.E.M.": {"aliases": ["REM"]}}})
    profiles = AlbumProfiles.from_config(
        {"albums": {"REM": {"Murmur": {"tags": ["jangle-pop"]}}}}, resolver=resolver
    )
    assert profiles.tags_for("R.E.M.", "Murmur") == {"jangle-pop"}


def test_album_tags_expand_through_the_tag_closure(tag_config):
    """Regression: an album tag reached only playlists naming that exact tag."""
    from music_engine.core import PlaylistMatcher, artist_profiles, canonical_tags
    from music_engine.identity import IdentityResolver

    closure, aliases = canonical_tags(tag_config)
    profiles = AlbumProfiles.from_config(
        {
            "patterns": [{"name": "p", "album_matches": "^Swing Session", "tags": ["swing"]}],
            "albums": {"Hole": {"Unplugged": {"tags": ["alternative"]}}},
        },
        tag_aliases=aliases,
        tag_closure=closure,
    )
    assert profiles.tags_for("Hole", "Unplugged") == {"college-rock", "rock", "music"}
    assert profiles.tags_for("Hole", "Swing Session 1") == {"swing", "jazz", "music"}

    resolver = IdentityResolver()
    rock = PlaylistMatcher(
        {"name": "Rock", "match_any_tags": ["rock"]},
        artist_profiles({"artists": {"Hole": {}}}, tag_config, resolver),
        resolver,
        profiles,
    )
    assert rock.artist_allows("Hole", "Unplugged")
    assert not rock.artist_allows("Hole", "Celebrity Skin")


def test_clear_tags_does_not_expand_to_ancestors(tag_config):
    from music_engine.core import canonical_tags

    closure, aliases = canonical_tags(tag_config)
    profiles = AlbumProfiles.from_config(
        {"albums": {"Hole": {"X": {"clear_tags": ["college-rock"]}}}},
        tag_aliases=aliases,
        tag_closure=closure,
    )
    assert profiles.cleared_tags_for("Hole", "X") == {"college-rock"}
