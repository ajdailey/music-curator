"""Recording classification, duplicate collapsing, and version preference."""

from __future__ import annotations

from music_engine.versions import (
    ALTERNATE,
    BOX_SET,
    COMPILATION,
    DELUXE,
    DEMO,
    LIVE,
    ORIGINAL,
    RADIO_EDIT,
    REMASTER,
    REMIX,
    STUDIO,
    base_title,
    classify,
    preference_key,
    select_versions,
    version_statistics,
)
from tests.conftest import make_track


def test_classifies_studio_original_by_default():
    info = classify(make_track("A", "Debut", "Song"))
    assert info.performance == STUDIO
    assert info.release == ORIGINAL
    assert info.is_studio


def test_flags_take_precedence_for_live():
    assert classify(make_track("A", "Album", "Song", flags=("live",))).performance == LIVE


def test_detects_live_from_title_and_album_text():
    assert classify(make_track("A", "Album", "Song (Live at Leeds)")).performance == LIVE
    assert classify(make_track("A", "Live in Tokyo", "Song")).performance == LIVE


def test_live_detection_does_not_misfire_on_song_titles():
    """'Live and Let Die' and 'Live Wire' are studio songs."""
    assert classify(make_track("A", "Album", "Live and Let Die")).performance == STUDIO
    assert classify(make_track("A", "Album", "Live Wire")).performance == STUDIO


def test_detects_live_before_a_sanitized_colon():
    """Filenames store ":" as "_", and "_" is a regex word character."""
    assert classify(make_track("A", "Album", "Song (Live_ London 2013)")).performance == LIVE
    assert classify(make_track("A", "Full House _Live_", "Song")).performance == LIVE


def test_underscore_boundary_keeps_title_exclusions_and_whole_words():
    assert classify(make_track("A", "Album", "Live Wire_ Remastered")).performance == STUDIO
    assert classify(make_track("A", "Oliver_ Twist", "Delivered")).performance == STUDIO
    assert classify(make_track("A", "Album", "Demolition_ Man")).performance == STUDIO
    assert classify(make_track("A", "Album", "Song (Demo_ Take 2)")).performance == DEMO
    single = make_track("A", "Album", "Song (Single Version_ Mono)")
    assert classify(single).performance == RADIO_EDIT


def test_classifies_demo_alternate_and_radio_edit():
    assert classify(make_track("A", "Album", "Song (Demo)")).performance == DEMO
    assert classify(make_track("A", "Album", "Song (Alternate Take)")).performance == ALTERNATE
    assert classify(make_track("A", "Album", "Song (Radio Edit)")).performance == RADIO_EDIT


def test_classifies_remix_from_title_and_album_text():
    assert classify(make_track("A", "Album", "Song (Remix)")).performance == REMIX
    assert classify(make_track("A", "Album", "Song (Todd Terry Remix)")).performance == REMIX
    assert classify(make_track("A", "Album", "Song - Remixed")).performance == REMIX
    assert classify(make_track("A", "Album", "Song (Club Mix)")).performance == REMIX
    assert classify(make_track("A", "Album", "Song (Extended Mix)")).performance == REMIX
    assert classify(make_track("A", "The Remixes", "Song")).performance == REMIX


def test_remix_detection_does_not_misfire_on_ordinary_mixes():
    """A mix that is the record as released is not a reworking of it."""
    for title in (
        "Song (Original Mix)",
        "Song (Mono Mix)",
        "Song (Stereo Mix)",
        "Song (Final Mix)",
        "Song (Rough Mix)",
        "Mixed Emotions",
    ):
        assert classify(make_track("A", "Album", title)).performance == STUDIO, title


def test_remix_flag_takes_precedence():
    assert classify(make_track("A", "Album", "Song", flags=("remix",))).performance == REMIX


def test_classifies_release_context():
    assert classify(make_track("A", "Album (Deluxe Edition)", "S")).release == DELUXE
    assert classify(make_track("A", "Album (Remastered)", "S")).release == REMASTER
    assert classify(make_track("A", "The Complete Box Set", "S")).release == BOX_SET
    assert classify(make_track("A", "Greatest Hits", "S")).release == COMPILATION
    assert classify(make_track("A", "The Best Of A", "S")).release == COMPILATION


def test_release_markers_end_at_a_sanitized_colon():
    assert classify(make_track("A", "Greatest Hits_ Volume One", "S")).release == COMPILATION
    assert classify(make_track("A", "Album", "S (Remastered_ 2009)")).release == REMASTER


def test_enja_heritage_collection_is_not_a_compilation():
    """The series name reissues original albums."""
    album = "The Enja Heritage Collection_ Soul Eyes"
    assert classify(make_track("A", album, "S")).release == ORIGINAL


def test_compilation_flag_outranks_other_release_markers():
    track = make_track("A", "Album (Deluxe Edition)", "S", flags=("compilation",))
    assert classify(track).release == COMPILATION


def test_base_title_strips_version_markers():
    assert base_title("Song (Live at Leeds)") == "song"
    assert base_title("Song - Remastered 2011") == "song"
    assert base_title("Song - Remaster_ Version 2") == "song"
    assert base_title("Song (Alternate Take)") == "song"
    assert base_title("Song") == "song"


def test_base_title_groups_remixes_with_the_original():
    """A remix must land in the same group, or it can never lose to the original."""
    assert base_title("Song (Remix)") == "song"
    assert base_title("Song (Todd Terry Remix)") == "song"
    assert base_title("Song - Remix") == "song"
    assert base_title("Song - Todd Terry Remix") == "song"
    assert base_title("Song (Club Mix)") == "song"
    assert base_title("Song - Extended Mix") == "song"


def test_base_title_keeps_meaningful_parentheses():
    assert base_title("Song (Part Two)") == "song (part two)"


def test_studio_original_wins_over_every_alternative():
    tracks = [
        make_track("A", "Live in Tokyo", "Song", flags=("live",)),
        make_track("A", "Greatest Hits", "Song"),
        make_track("A", "Album (Deluxe Edition)", "Song"),
        make_track("A", "Album", "Song"),
    ]
    selection = select_versions(tracks, max_live_versions_per_song=0)
    assert len(selection.tracks) == 1
    assert selection.tracks[0].album == "Album"
    assert selection.removed == 3


def test_the_original_always_beats_a_remix():
    """The reported symptom: remixes standing in for the actual song."""
    tracks = [
        make_track("A", "The Remixes", "Song (Club Mix)"),
        make_track("A", "The Remixes", "Song (Todd Terry Remix)"),
        make_track("A", "Album", "Song"),
    ]
    selection = select_versions(tracks, max_live_versions_per_song=0)
    assert [t.title for t in selection.tracks] == ["Song"]


def test_a_remix_still_survives_when_it_is_the_only_recording():
    """Collapsing never empties a song out of the catalog entirely."""
    tracks = [make_track("A", "The Remixes", "Song (Club Mix)")]
    selection = select_versions(tracks, max_live_versions_per_song=0)
    assert len(selection.tracks) == 1


def test_remix_stays_demoted_even_when_studio_is_not_preferred():
    """prefer_studio governs the studio/live tradeoff, not remix substitution."""
    original = make_track("A", "Album", "Song")
    remix = make_track("A", "The Remixes", "Song (Club Mix)")
    ranked = sorted(
        [remix, original], key=lambda t: preference_key(t, "seed", prefer_studio=False)
    )
    assert ranked[0] is original


def test_a_remix_is_not_kept_as_the_extra_live_version():
    tracks = [
        make_track("A", "Album", "Song"),
        make_track("A", "The Remixes", "Song (Club Mix)"),
    ]
    selection = select_versions(tracks, max_live_versions_per_song=1)
    assert [t.title for t in selection.tracks] == ["Song"]


def test_one_live_version_is_retained_alongside_the_studio_take():
    """Studio first, not studio only (Musical Constitution #3)."""
    tracks = [
        make_track("A", "Album", "Song"),
        make_track("A", "Live in Tokyo", "Song", flags=("live",)),
        make_track("A", "Live in Paris", "Song", flags=("live",)),
        make_track("A", "Live in Berlin", "Song", flags=("live",)),
    ]
    selection = select_versions(tracks, max_live_versions_per_song=1)
    albums = sorted(t.album for t in selection.tracks)
    assert len(selection.tracks) == 2
    assert "Album" in albums
    assert selection.live_kept == 1


def test_different_songs_are_never_collapsed():
    tracks = [make_track("A", "Album", "Song One"), make_track("A", "Album", "Song Two")]
    assert len(select_versions(tracks).tracks) == 2


def test_same_title_by_different_artists_is_never_collapsed():
    tracks = [make_track("A", "Album", "Song"), make_track("B", "Album", "Song")]
    assert len(select_versions(tracks).tracks) == 2


def test_identity_map_collapses_alias_spellings():
    tracks = [
        make_track("R.E.M.", "Murmur", "Radio Free Europe"),
        make_track("R.E.M_", "Eponymous", "Radio Free Europe"),
    ]
    selection = select_versions(
        tracks, identity_of={"R.E.M.": "R.E.M.", "R.E.M_": "R.E.M."}
    )
    assert len(selection.tracks) == 1


def test_selection_is_deterministic_regardless_of_input_order():
    tracks = [
        make_track("A", f"Album {i}", "Song", flags=("live",) if i % 2 else ())
        for i in range(6)
    ]
    first = select_versions(tracks, seed="s").tracks
    second = select_versions(list(reversed(tracks)), seed="s").tracks
    assert [t.relative_path for t in first] == [t.relative_path for t in second]


def test_version_statistics_counts_every_track():
    tracks = [
        make_track("A", "Album", "One"),
        make_track("A", "Live in Tokyo", "Two", flags=("live",)),
    ]
    stats = version_statistics(tracks)
    assert stats["performance"] == {LIVE: 1, STUDIO: 1}
    assert sum(stats["release"].values()) == 2


def test_base_title_keeps_reprise_and_instrumental_distinct():
    """A reprise or an instrumental is a different piece from the song."""
    assert base_title("Sgt. Pepper (Reprise)") != base_title("Sgt. Pepper")
    assert base_title("Song (Instrumental)") != base_title("Song")
    assert base_title("Song (Instrumental Version)") != base_title("Song")
    assert base_title("Song - Instrumental") != base_title("Song")
    # The marker survives when another one is stripped beside it.
    assert base_title("Song (Reprise) (Remastered)") == base_title("Song (Reprise)")


def test_cleared_flag_overrides_flag_and_text_detection():
    from dataclasses import replace

    track = make_track("Hole", "Live Through This", "Violet", flags=("live",))
    assert classify(track).performance == LIVE
    cleared = replace(track, flags=frozenset(), cleared_flags=frozenset({"live"}))
    assert classify(cleared).performance == STUDIO
