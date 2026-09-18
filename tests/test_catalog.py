"""Library export parsing: filenames are the source of truth."""

from __future__ import annotations

import pytest

from music_engine.catalog import derive_flags, parse_filename, read_catalog

ROOT = "M:\\itunes_portable\\iTunes\\iTunes Media\\Music\\"


def export(tmp_path, entries: list[tuple[str, str]]):
    """entries: (extinf_display, windows_path)"""
    lines = ["#EXTM3U", "#PLAYLIST:Library Playlist"]
    for display, path in entries:
        lines.append(f"#EXTINF:200,{display}")
        lines.append(path)
    target = tmp_path / "Library Playlist.m3u8"
    target.write_text("\ufeff" + "\n".join(lines) + "\n", encoding="utf-8")
    return target


# --- filename parsing -----------------------------------------------------


def test_parses_plain_track_number():
    assert parse_filename("01 Radio Free Europe") == (None, 1, "Radio Free Europe")


def test_parses_disc_prefixed_track_number():
    """Regression: the disc prefix must not stay in the title as '01 Humph'."""
    assert parse_filename("1-01 Humph") == (1, 1, "Humph")
    assert parse_filename("2.05 Blue Monk") == (2, 5, "Blue Monk")


def test_filename_without_track_number_keeps_full_title():
    assert parse_filename("Untitled Hidden Track") == (None, None, "Untitled Hidden Track")


def test_title_containing_digits_is_preserved():
    assert parse_filename("07 1979") == (None, 7, "1979")


# --- flag derivation ------------------------------------------------------


def test_derives_live_and_release_flags():
    assert "live" in derive_flags("Live at Leeds", "Song")
    assert "deluxe" in derive_flags("Album (Deluxe Edition)", "Song")
    assert "remaster" in derive_flags("Album", "Song (2011 Remastered)")
    assert "compilation" in derive_flags("Greatest Hits", "Song")
    assert "demo" in derive_flags("Album", "Song (Demo)")
    assert "remix" in derive_flags("Album", "Song (Club Mix)")
    assert "remix" in derive_flags("The Remixes", "Song")


def test_does_not_flag_songs_merely_titled_live():
    assert "live" not in derive_flags("Album", "Live and Let Die")
    assert "live" not in derive_flags("Album", "Live Wire")


def test_underscore_from_a_sanitized_colon_ends_a_marker():
    """Filenames store ":" as "_", and "_" is a regex word character."""
    assert "live" in derive_flags("Album", "Song (Live_ London 2013)")
    assert "live" in derive_flags("Album", "Song (1997_Live)")
    assert "demo" in derive_flags("Album", "Song (Demo_ 2019 Remaster)")
    assert "remaster" in derive_flags("Album", "Song (2017 Remaster_ Bonus)")
    assert "remix" in derive_flags("Album", "Song (US Remix_ 2009)")
    assert "acoustic" in derive_flags("Album", "Song (Live_Acoustic)")
    assert "compilation" in derive_flags("Greatest Hits_ Volume One", "Song")


def test_underscore_boundary_keeps_title_exclusions_and_whole_words():
    assert "live" not in derive_flags("Album", "Live Wire_ Remastered")
    assert "live" not in derive_flags("Album", "Live and Let Die")
    assert "live" not in derive_flags("Oliver_ Twist", "Delivered")
    assert "live" not in derive_flags("Album", "Alive_ Again")
    assert "demo" not in derive_flags("Album", "Demolition_ Man")


def test_does_not_flag_an_ordinary_mix_as_a_remix():
    assert "remix" not in derive_flags("Album", "Song (Original Mix)")
    assert "remix" not in derive_flags("Album", "Song (Mono Mix)")
    assert "remix" not in derive_flags("Album", "Mixed Emotions")


def test_studio_track_has_no_flags():
    assert derive_flags("Murmur", "Radio Free Europe") == frozenset()


# --- export parsing -------------------------------------------------------


def test_reads_artist_album_and_title_from_the_path(tmp_path):
    path = export(tmp_path, [("Whatever - Display", ROOT + "R.E.M_\\Murmur\\01 Radio.m4a")])
    tracks = read_catalog(path, ROOT)
    assert len(tracks) == 1
    track = tracks[0]
    assert track.artist == "R.E.M_"
    assert track.album == "Murmur"
    assert track.title == "Radio"
    assert track.track_no == 1
    assert track.relative_path == "R.E.M_\\Murmur\\01 Radio.m4a"


def test_extinf_display_is_ignored_for_titles(tmp_path):
    """A title containing ' - ' makes the display line ambiguous."""
    path = export(
        tmp_path, [("Artist - Song - Part Two", ROOT + "Artist\\Album\\03 Song - Part Two.m4a")]
    )
    track = read_catalog(path, ROOT)[0]
    assert track.artist == "Artist"
    assert track.title == "Song - Part Two"


def test_paths_outside_the_library_root_are_skipped(tmp_path):
    path = export(
        tmp_path,
        [
            ("A - B", "Z:\\elsewhere\\Artist\\Album\\01 Song.m4a"),
            ("A - B", ROOT + "Artist\\Album\\01 Song.m4a"),
        ],
    )
    assert len(read_catalog(path, ROOT)) == 1


def test_forward_slash_paths_are_accepted(tmp_path):
    path = export(tmp_path, [("A - B", ROOT.replace("\\", "/") + "Artist/Album/01 Song.m4a")])
    tracks = read_catalog(path, ROOT)
    assert tracks[0].artist == "Artist"


def test_track_key_normalizes_for_portable_intersection(tmp_path):
    path = export(tmp_path, [("A - B", ROOT + "Artist\\Album\\01 Song.m4a")])
    assert read_catalog(path, ROOT)[0].key == "artist/album/01 song.m4a"


def test_flags_are_derived_during_parsing(tmp_path):
    path = export(tmp_path, [("A - B", ROOT + "Artist\\Live at Leeds\\01 Song.m4a")])
    assert "live" in read_catalog(path, ROOT)[0].flags


def test_missing_export_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Library export not found"):
        read_catalog(tmp_path / "absent.m3u8", ROOT)


def test_comments_and_blank_lines_are_ignored(tmp_path):
    target = tmp_path / "Library Playlist.m3u8"
    target.write_text(
        "#EXTM3U\n\n#PLAYLIST:x\n#EXTINF:1,A - B\n" + ROOT + "Artist\\Album\\01 S.m4a\n",
        encoding="utf-8",
    )
    assert len(read_catalog(target, ROOT)) == 1


def test_crlf_export_parses_like_lf(tmp_path):
    path = tmp_path / "Library Playlist.m3u8"
    lines = [
        "#EXTM3U",
        "#EXTINF:200,Artist - Song",
        ROOT + "Artist\\Album\\1-02 Song.m4a",
    ]
    path.write_bytes(("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8"))
    (track,) = read_catalog(path, ROOT)
    assert (track.artist, track.album, track.title) == ("Artist", "Album", "Song")
    assert (track.disc_no, track.track_no) == (1, 2)
    assert not track.relative_path.endswith("\r")
