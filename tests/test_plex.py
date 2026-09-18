"""Plex integration: configuration safety, resolution, diffing, and sync gates.

None of these tests touch a real server. A fake client stands in for plexapi so
the safety rules are verifiable without credentials.
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from music_engine import plex_cli
from music_engine.integrations.plex.cache import SCHEMA_VERSION, PlexIndex, cache_path
from music_engine.integrations.plex.config import (
    ENV_LIBRARY,
    ENV_TOKEN,
    ENV_URL,
    ENV_VERIFY_SSL,
    MANAGED_PREFIX,
    PlexConfigurationError,
    PlexSettings,
    load_dotenv,
)
from music_engine.integrations.plex.models import (
    AMBIGUOUS,
    MATCHED,
    UNRESOLVED,
    ManifestEntry,
    PlexTrack,
)
from music_engine.integrations.plex.resolver import PlexResolver, path_suffix
from music_engine.integrations.plex.synchronizer import (
    PlexSynchronizer,
    SyncThresholds,
    diff_playlist,
    is_managed,
    managed_title,
)

TOKEN = "supersecrettoken1234"


# --- configuration and secret handling ------------------------------------


def test_missing_token_fails_with_a_clear_message(tmp_path):
    with pytest.raises(PlexConfigurationError) as exc:
        PlexSettings.from_env(tmp_path, environ={ENV_URL: "https://host:32400"})
    message = str(exc.value)
    assert ENV_TOKEN in message
    assert ".env" in message


def test_missing_variables_are_reported_together(tmp_path):
    with pytest.raises(PlexConfigurationError) as exc:
        PlexSettings.from_env(tmp_path, environ={})
    message = str(exc.value)
    assert ENV_URL in message and ENV_TOKEN in message


def test_there_is_no_default_token(tmp_path):
    """A missing token must never fall back to a value."""
    with pytest.raises(PlexConfigurationError):
        PlexSettings.from_env(tmp_path, environ={ENV_URL: "http://h:32400", ENV_TOKEN: "  "})


def test_token_is_never_shown_in_repr_or_describe(tmp_path):
    settings = PlexSettings.from_env(
        tmp_path, environ={ENV_URL: "http://h:32400", ENV_TOKEN: TOKEN}
    )
    assert TOKEN not in repr(settings)
    assert TOKEN not in json.dumps(settings.describe())
    assert TOKEN not in str(settings.describe())


def test_dotenv_populates_missing_variables(tmp_path):
    (tmp_path / ".env").write_text(
        f"# comment\nexport {ENV_URL}=http://h:32400\n{ENV_TOKEN}='{TOKEN}'\n",
        encoding="utf-8",
    )
    settings = PlexSettings.from_env(tmp_path, environ={})
    assert settings.url == "http://h:32400"
    assert settings.token == TOKEN


def test_real_environment_overrides_dotenv(tmp_path):
    (tmp_path / ".env").write_text(f"{ENV_URL}=http://from-file:32400\n", encoding="utf-8")
    settings = PlexSettings.from_env(
        tmp_path, environ={ENV_URL: "http://from-env:32400", ENV_TOKEN: TOKEN}
    )
    assert settings.url == "http://from-env:32400"


def test_dotenv_parses_quotes_and_ignores_junk(tmp_path):
    (tmp_path / ".env").write_text(
        '\n# c\nA="one"\nB=two\nnot-a-pair\nC=\n', encoding="utf-8"
    )
    assert load_dotenv(tmp_path / ".env") == {"A": "one", "B": "two", "C": ""}


def test_ssl_verification_defaults_off_for_https_and_on_otherwise(tmp_path):
    """A private-address Plex server usually has a self-signed certificate."""
    https = PlexSettings.from_env(
        tmp_path, environ={ENV_URL: "https://192.168.1.5:32400", ENV_TOKEN: TOKEN}
    )
    http = PlexSettings.from_env(
        tmp_path, environ={ENV_URL: "http://192.168.1.5:32400", ENV_TOKEN: TOKEN}
    )
    assert https.verify_ssl is False
    assert http.verify_ssl is True


def test_ssl_verification_can_be_forced_on(tmp_path):
    settings = PlexSettings.from_env(
        tmp_path,
        environ={ENV_URL: "https://h:32400", ENV_TOKEN: TOKEN, ENV_VERIFY_SSL: "true"},
    )
    assert settings.verify_ssl is True


def test_library_defaults_to_music(tmp_path):
    settings = PlexSettings.from_env(
        tmp_path, environ={ENV_URL: "http://h:32400", ENV_TOKEN: TOKEN}
    )
    assert settings.library == "Music"
    settings = PlexSettings.from_env(
        tmp_path,
        environ={ENV_URL: "http://h:32400", ENV_TOKEN: TOKEN, ENV_LIBRARY: "Tunes"},
    )
    assert settings.library == "Tunes"


# --- resolution -----------------------------------------------------------


def plex_track(rating_key, artist, album, title, path, **kwargs):
    return PlexTrack(
        rating_key=str(rating_key),
        artist=artist,
        album_artist=artist,
        album=album,
        title=title,
        media_path=path,
        **kwargs,
    )


def entry(position, artist, album, title, relative, plex_path="", **kwargs):
    return ManifestEntry(
        position=position,
        catalog_id=relative.casefold(),
        artist=artist,
        album=album,
        title=title,
        relative_path=relative,
        plex_path=plex_path or f"/music_portable/{relative}",
        **kwargs,
    )


def test_resolves_by_exact_media_path():
    tracks = [plex_track(1, "A", "Al", "S", "/music_portable/A/Al/01 S.m4a")]
    resolver = PlexResolver(tracks)
    result = resolver.resolve_entry(entry(1, "A", "Al", "S", "A/Al/01 S.m4a"))
    assert result.status == MATCHED
    assert result.plex_track.rating_key == "1"
    assert result.confidence == 1.0


def test_resolves_when_plex_uses_a_different_mount_root():
    """Plex may report a different root than the catalog holds."""
    tracks = [plex_track(1, "A", "Al", "S", "/mnt/media/music/A/Al/01 S.m4a")]
    resolver = PlexResolver(tracks)
    result = resolver.resolve_entry(entry(1, "A", "Al", "S", "A/Al/01 S.m4a"))
    assert result.status == MATCHED


def test_path_matching_is_case_and_separator_insensitive():
    tracks = [plex_track(1, "A", "Al", "S", "\\music_portable\\A\\Al\\01 S.M4A")]
    resolver = PlexResolver(tracks)
    assert resolver.resolve_entry(entry(1, "A", "Al", "S", "A/Al/01 S.m4a")).status == MATCHED


def test_resolves_by_metadata_when_paths_are_absent():
    tracks = [
        PlexTrack(
            rating_key="7",
            artist="A",
            album_artist="A",
            album="Al",
            title="Song",
            track_number=3,
            disc_number=1,
        )
    ]
    resolver = PlexResolver(tracks)
    result = resolver.resolve_entry(
        entry(1, "A", "Al", "Song", "A/Al/03 Song.m4a", plex_path="", track_no=3, disc_no=1)
    )
    assert result.status == MATCHED
    assert result.method == "metadata"
    assert result.confidence < 1.0


def test_identical_titles_across_albums_do_not_resolve_by_title_alone():
    """Title-only matching would attach the wrong recording."""
    tracks = [
        PlexTrack(rating_key="1", artist="A", album_artist="A", album="One", title="Song"),
        PlexTrack(rating_key="2", artist="A", album_artist="A", album="Two", title="Song"),
    ]
    resolver = PlexResolver(tracks)
    result = resolver.resolve_entry(
        entry(1, "A", "Three", "Song", "A/Three/01 Song.m4a", plex_path="")
    )
    assert result.status == UNRESOLVED


def test_duplicate_paths_are_reported_ambiguous_not_guessed():
    tracks = [
        plex_track(1, "A", "Al", "S", "/music_portable/A/Al/01 S.m4a"),
        plex_track(2, "A", "Al", "S", "/music_portable/A/Al/01 S.m4a"),
    ]
    resolver = PlexResolver(tracks)
    result = resolver.resolve_entry(entry(1, "A", "Al", "S", "A/Al/01 S.m4a"))
    assert result.status == AMBIGUOUS
    assert len(result.candidates) == 2
    assert result.confidence == 0.0


def test_missing_track_is_unresolved():
    resolver = PlexResolver([])
    result = resolver.resolve_entry(entry(1, "A", "Al", "S", "A/Al/01 S.m4a"))
    assert result.status == UNRESOLVED


def test_live_and_studio_versions_resolve_to_different_tracks():
    tracks = [
        plex_track(1, "A", "Studio", "Song", "/music_portable/A/Studio/01 Song.m4a"),
        plex_track(2, "A", "Live 1975", "Song", "/music_portable/A/Live 1975/01 Song.m4a"),
    ]
    resolver = PlexResolver(tracks)
    studio = resolver.resolve_entry(entry(1, "A", "Studio", "Song", "A/Studio/01 Song.m4a"))
    live = resolver.resolve_entry(entry(2, "A", "Live 1975", "Song", "A/Live 1975/01 Song.m4a"))
    assert studio.plex_track.rating_key == "1"
    assert live.plex_track.rating_key == "2"


def test_summary_counts_and_ordering():
    tracks = [plex_track(1, "A", "Al", "S1", "/music_portable/A/Al/01 S1.m4a")]
    resolver = PlexResolver(tracks)
    summary = resolver.resolve(
        "P",
        [
            entry(1, "A", "Al", "S1", "A/Al/01 S1.m4a"),
            entry(2, "A", "Al", "S2", "A/Al/02 S2.m4a"),
        ],
    )
    assert summary.desired == 2 and summary.matched == 1 and summary.unresolved == 1
    assert summary.rating_keys == ["1"]
    assert summary.match_rate == 0.5


def test_path_suffix_ignores_differing_roots():
    assert path_suffix("/a/b/Artist/Album/01 S.m4a") == path_suffix(
        "/other/Artist/Album/01 S.m4a"
    )


# --- index cache ----------------------------------------------------------


def test_index_round_trips(tmp_path):
    index = PlexIndex(
        tracks=[plex_track(1, "A", "Al", "S", "/p/A/Al/01 S.m4a")],
        created_at=1000.0,
        server_identifier="abc",
        library="Music",
    )
    path = tmp_path / "index.json"
    index.save(path)
    loaded = PlexIndex.load(path)
    assert len(loaded.tracks) == 1
    assert loaded.matches("abc", "Music")
    assert not loaded.matches("other", "Music")


def test_corrupt_index_is_ignored(tmp_path):
    path = tmp_path / "index.json"
    path.write_text("{not json", encoding="utf-8")
    assert PlexIndex.load(path) is None


def test_index_from_an_old_schema_is_ignored(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION - 1}), encoding="utf-8")
    assert PlexIndex.load(path) is None


# --- ownership and diffing ------------------------------------------------


def test_managed_titles_carry_the_prefix():
    assert managed_title("Grunge") == f"{MANAGED_PREFIX}Grunge"
    assert is_managed(f"{MANAGED_PREFIX}Grunge")
    assert not is_managed("My Own Mix")


def test_diff_detects_a_missing_playlist():
    resolver = PlexResolver([plex_track(1, "A", "Al", "S", "/music_portable/A/Al/01 S.m4a")])
    summary = resolver.resolve("P", [entry(1, "A", "Al", "S", "A/Al/01 S.m4a")])
    difference = diff_playlist("P", summary, [], exists=False)
    assert not difference.exists and difference.to_add == 1 and not difference.in_sync


def test_diff_detects_reordering_only():
    tracks = [
        plex_track(1, "A", "Al", "S1", "/music_portable/A/Al/01 S1.m4a"),
        plex_track(2, "A", "Al", "S2", "/music_portable/A/Al/02 S2.m4a"),
    ]
    resolver = PlexResolver(tracks)
    summary = resolver.resolve(
        "P",
        [entry(1, "A", "Al", "S1", "A/Al/01 S1.m4a"), entry(2, "A", "Al", "S2", "A/Al/02 S2.m4a")],
    )
    difference = diff_playlist("P", summary, ["2", "1"], exists=True)
    assert difference.to_add == 0 and difference.to_remove == 0
    assert difference.reorder_required and not difference.in_sync


def test_identical_playlist_is_in_sync():
    resolver = PlexResolver([plex_track(1, "A", "Al", "S", "/music_portable/A/Al/01 S.m4a")])
    summary = resolver.resolve("P", [entry(1, "A", "Al", "S", "A/Al/01 S.m4a")])
    assert diff_playlist("P", summary, ["1"], exists=True).in_sync


# --- synchronization safety ----------------------------------------------


class FakePlexClient:
    """Stand-in for the real client, recording every mutation."""

    def __init__(self, playlists=None):
        self.playlists_data = dict(playlists or {})
        self.actions: list[tuple] = []

    def playlist(self, title):
        return title if title in self.playlists_data else None

    def playlist_items(self, title):
        return list(self.playlists_data.get(title, []))

    def create_playlist(self, title, rating_keys):
        if not rating_keys:
            raise ValueError("empty")
        self.playlists_data[title] = list(rating_keys)
        self.actions.append(("create", title, tuple(rating_keys)))
        return title

    def delete_playlist(self, title):
        if not title.startswith(MANAGED_PREFIX):
            raise ValueError(f"unmanaged {title}")
        existed = self.playlists_data.pop(title, None) is not None
        self.actions.append(("delete", title))
        return existed

    def rename_playlist(self, title, new_title):
        for name in (title, new_title):
            if not name.startswith(MANAGED_PREFIX):
                raise ValueError(f"unmanaged {name}")
        self.playlists_data[new_title] = self.playlists_data.pop(title)
        self.actions.append(("rename", title, new_title))
        return True


def build(client, tracks, thresholds=None):
    return PlexSynchronizer(client, PlexResolver(tracks), thresholds)


def sample_entries(n=4):
    return [entry(i, "A", "Al", f"S{i}", f"A/Al/0{i} S{i}.m4a") for i in range(1, n + 1)]


def sample_tracks(n=4):
    return [
        plex_track(i, "A", "Al", f"S{i}", f"/music_portable/A/Al/0{i} S{i}.m4a")
        for i in range(1, n + 1)
    ]


def test_dry_run_makes_no_changes():
    client = FakePlexClient()
    synchronizer = build(client, sample_tracks())
    outcome = synchronizer.sync("Grunge", sample_entries(), apply=False)
    assert outcome.action == "create" and not outcome.applied
    assert client.actions == []


def test_apply_creates_and_verifies_the_playlist():
    client = FakePlexClient()
    synchronizer = build(client, sample_tracks())
    outcome = synchronizer.sync("Grunge", sample_entries(), apply=True)
    assert outcome.applied and outcome.verified
    assert client.playlists_data[f"{MANAGED_PREFIX}Grunge"] == ["1", "2", "3", "4"]


def test_apply_preserves_generated_order():
    client = FakePlexClient()
    synchronizer = build(client, sample_tracks())
    entries = list(reversed(sample_entries()))
    for position, item in enumerate(entries, start=1):
        object.__setattr__(item, "position", position)
    synchronizer.sync("Grunge", entries, apply=True)
    assert client.playlists_data[f"{MANAGED_PREFIX}Grunge"] == ["4", "3", "2", "1"]


def test_sync_refuses_when_too_many_tracks_are_unresolved():
    client = FakePlexClient()
    synchronizer = build(client, sample_tracks(1))  # only one of four resolves
    outcome = synchronizer.sync("Grunge", sample_entries(4), apply=True)
    assert outcome.action == "skip" and not outcome.applied
    assert "unresolved" in outcome.reason
    assert client.actions == []


def test_sync_refuses_an_empty_playlist():
    client = FakePlexClient()
    synchronizer = build(client, [])
    outcome = synchronizer.sync("Grunge", [], apply=True)
    assert outcome.action == "skip" and "empty" in outcome.reason


def test_populated_playlist_is_never_replaced_by_an_empty_one():
    client = FakePlexClient({f"{MANAGED_PREFIX}Grunge": ["9", "8"]})
    synchronizer = build(client, [])
    outcome = synchronizer.sync("Grunge", [], apply=True)
    assert not outcome.applied
    assert client.playlists_data[f"{MANAGED_PREFIX}Grunge"] == ["9", "8"]


def test_create_only_leaves_an_existing_playlist_alone():
    client = FakePlexClient({f"{MANAGED_PREFIX}Grunge": ["9"]})
    synchronizer = build(client, sample_tracks())
    outcome = synchronizer.sync("Grunge", sample_entries(), apply=True, create_only=True)
    assert outcome.action == "skip"
    assert client.playlists_data[f"{MANAGED_PREFIX}Grunge"] == ["9"]


def test_up_to_date_playlist_is_not_rewritten():
    client = FakePlexClient({f"{MANAGED_PREFIX}Grunge": ["1", "2", "3", "4"]})
    synchronizer = build(client, sample_tracks())
    outcome = synchronizer.sync("Grunge", sample_entries(), apply=True)
    assert outcome.action == "up-to-date" and not outcome.applied
    assert client.actions == []


def test_unmanaged_playlists_are_never_written():
    client = FakePlexClient()
    synchronizer = build(client, sample_tracks())
    with pytest.raises(ValueError, match="unmanaged"):
        synchronizer._write("Someone Else's Mix", ["1"])


GRUNGE = f"{MANAGED_PREFIX}Grunge"
STAGING = f"{GRUNGE} (syncing)"
PREVIOUS = f"{GRUNGE} (previous)"


def test_replace_keeps_the_old_playlist_until_the_new_one_is_verified():
    client = FakePlexClient({GRUNGE: ["9"]})
    build(client, sample_tracks()).sync("Grunge", sample_entries(), apply=True)
    assert client.actions == [
        ("create", STAGING, ("1", "2", "3", "4")),
        ("rename", GRUNGE, PREVIOUS),
        ("rename", STAGING, GRUNGE),
        ("delete", PREVIOUS),
    ]
    assert client.playlists_data == {GRUNGE: ["1", "2", "3", "4"]}


def test_interrupted_swap_keeps_the_old_playlist_and_a_rerun_recovers():
    client = FakePlexClient({GRUNGE: ["9"]})
    rename = client.rename_playlist

    class Interrupted(Exception):
        pass

    def failing_rename(title, new_title):
        if title == STAGING:
            raise Interrupted
        return rename(title, new_title)

    client.rename_playlist = failing_rename
    with pytest.raises(Interrupted):
        build(client, sample_tracks()).sync("Grunge", sample_entries(), apply=True)
    assert client.playlists_data[PREVIOUS] == ["9"]

    client.rename_playlist = rename
    outcome = build(client, sample_tracks()).sync("Grunge", sample_entries(), apply=True)
    assert outcome.verified
    assert client.playlists_data == {GRUNGE: ["1", "2", "3", "4"]}


def test_rerun_deletes_a_leftover_staging_copy():
    client = FakePlexClient({GRUNGE: ["9"], STAGING: ["1"]})
    build(client, sample_tracks()).sync("Grunge", sample_entries(), apply=True)
    assert client.actions[0] == ("delete", STAGING)
    assert client.playlists_data == {GRUNGE: ["1", "2", "3", "4"]}


def test_failed_append_removes_the_partial_staging_copy():
    client = FakePlexClient({GRUNGE: ["9"]})

    class AppendFailed(Exception):
        pass

    def partial_create(title, rating_keys):
        client.playlists_data[title] = list(rating_keys[:1])
        raise AppendFailed

    client.create_playlist = partial_create
    with pytest.raises(AppendFailed):
        build(client, sample_tracks()).sync("Grunge", sample_entries(), apply=True)
    assert client.playlists_data == {GRUNGE: ["9"]}


def test_thresholds_accept_a_clean_resolution():
    resolver = PlexResolver(sample_tracks())
    summary = resolver.resolve("P", sample_entries())
    ok, reason = SyncThresholds().evaluate(summary)
    assert ok and reason == "within thresholds"


# --- incremental indexing -------------------------------------------------


def test_partial_index_round_trips_resume_state(tmp_path):
    index = PlexIndex(
        tracks=[plex_track(1, "A", "Al", "S", "/p/A/Al/01 S.m4a")],
        created_at=1000.0,
        server_identifier="abc",
        library="Portable",
        complete=False,
        next_start=1,
        expected_total=500,
    )
    path = tmp_path / "index.json"
    index.save(path)
    loaded = PlexIndex.load(path)
    assert loaded.complete is False
    assert loaded.next_start == 1
    assert loaded.expected_total == 500


def test_index_defaults_to_complete_for_older_caches(tmp_path):
    """A cache without a ``complete`` field loads as complete."""
    path = tmp_path / "index.json"
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "tracks": [], "library": "P"}),
        encoding="utf-8",
    )
    assert PlexIndex.load(path).complete is True


class FakeSection:
    """Stands in for a plexapi library section, paging like the real one."""

    def __init__(self, count):
        self.count = count
        self.requests = []

    def search(self, libtype, container_start, container_size, maxresults):
        self.requests.append((container_start, container_size))
        page = []
        for i in range(container_start, min(container_start + container_size, self.count)):
            item = type(
                "Item",
                (),
                {
                    "ratingKey": i,
                    "grandparentTitle": "A",
                    "parentTitle": "Al",
                    "title": f"S{i}",
                    "year": None,
                    "parentIndex": 1,
                    "index": i,
                    "duration": 1000,
                    "media": [],
                    "guid": "",
                },
            )()
            page.append(item)
        return page


def test_indexing_pages_through_the_library(monkeypatch):
    from music_engine.integrations.plex import client as client_module

    section = FakeSection(1250)
    plex = client_module.PlexClient.__new__(client_module.PlexClient)
    monkeypatch.setattr(plex, "music_section", lambda: section, raising=False)

    collected = list(plex.iter_tracks(start=0, batch_size=500))
    assert len(collected) == 1250
    assert [index for index, _ in collected][:3] == [0, 1, 2]
    assert section.requests == [(0, 500), (500, 500), (1000, 500)]


def test_indexing_resumes_from_an_offset(monkeypatch):
    from music_engine.integrations.plex import client as client_module

    section = FakeSection(1000)
    plex = client_module.PlexClient.__new__(client_module.PlexClient)
    monkeypatch.setattr(plex, "music_section", lambda: section, raising=False)

    collected = list(plex.iter_tracks(start=900, batch_size=500))
    assert len(collected) == 100
    assert collected[0][0] == 900


# --- playlist creation batching -------------------------------------------


class FakeQueryServer:
    """Records the requests plexapi would issue when building a playlist."""

    def __init__(self):
        self.queries = []
        self._session = type("S", (), {"post": "POST", "put": "PUT"})()
        self.machineIdentifier = "abc123"

    def _uriRoot(self):
        return f"server://{self.machineIdentifier}/com.plexapp.plugins.library"

    def query(self, key, method=None):
        self.queries.append((method, key))
        import xml.etree.ElementTree as ET

        element = ET.Element("Playlist", {"ratingKey": "999", "title": "x"})
        return [element]


def client_with(server):
    from music_engine.integrations.plex.client import PlexClient

    client = PlexClient.__new__(PlexClient)
    client._server = server
    client.settings = None
    return client


def keys(count):
    return [str(100000 + i) for i in range(count)]


def test_large_playlist_is_created_in_batches(monkeypatch):
    """Regression: a 4,496-track playlist produced a 45 KB URL and Plex 400'd."""
    server = FakeQueryServer()
    client = client_with(server)

    class FakePlaylist:
        def __init__(self, *a, **kw):
            self.key = "/playlists/999"

    monkeypatch.setitem(
        __import__("sys").modules,
        "plexapi.playlist",
        type("M", (), {"Playlist": FakePlaylist}),
    )
    client.create_playlist("MC - Big", keys(1000), batch_size=200)

    posts = [q for q in server.queries if q[0] == "POST"]
    puts = [q for q in server.queries if q[0] == "PUT"]
    assert len(posts) == 1, "one create request"
    assert len(puts) == 4, "remaining 800 keys appended in 4 batches of 200"
    assert all(len(key) < 6000 for _, key in server.queries), "no oversized URL"


def test_small_playlist_needs_only_the_create_request(monkeypatch):
    server = FakeQueryServer()
    client = client_with(server)

    class FakePlaylist:
        def __init__(self, *a, **kw):
            self.key = "/playlists/999"

    monkeypatch.setitem(
        __import__("sys").modules,
        "plexapi.playlist",
        type("M", (), {"Playlist": FakePlaylist}),
    )
    client.create_playlist("MC - Small", keys(50), batch_size=200)
    assert len(server.queries) == 1


def test_every_rating_key_is_sent_exactly_once_and_in_order(monkeypatch):
    server = FakeQueryServer()
    client = client_with(server)

    class FakePlaylist:
        def __init__(self, *a, **kw):
            self.key = "/playlists/999"

    monkeypatch.setitem(
        __import__("sys").modules,
        "plexapi.playlist",
        type("M", (), {"Playlist": FakePlaylist}),
    )
    wanted = keys(450)
    client.create_playlist("MC - Order", wanted, batch_size=100)

    import re
    from urllib.parse import unquote

    sent = []
    for _, key in server.queries:
        match = re.search(r"library%2Fmetadata%2F([^&]*)", key) or re.search(
            r"library/metadata/([^&]*)", key
        )
        sent.extend(unquote(match.group(1)).split(","))
    assert sent == wanted


def test_empty_playlist_is_still_refused():
    client = client_with(FakeQueryServer())
    with pytest.raises(ValueError, match="Refusing to create empty playlist"):
        client.create_playlist("MC - Empty", [])


# --- command line ---------------------------------------------------------


def cli_settings():
    return PlexSettings(url="http://h:32400", token=TOKEN, library="Music")


class FakeServer:
    def __init__(self, identifier):
        self.machineIdentifier = identifier


class FakeIndexClient:
    """Pages `count` tracks; raises KeyboardInterrupt before `interrupt_at`."""

    def __init__(self, count, identifier="abc", interrupt_at=None, total=None):
        self.count, self.interrupt_at = count, interrupt_at
        self.total = count if total is None else total
        self.server = FakeServer(identifier)

    def track_total(self):
        return self.total

    def iter_tracks(self, start=0, batch_size=500):
        for i in range(start, self.count):
            if i == self.interrupt_at:
                raise KeyboardInterrupt
            yield i, plex_track(i, "A", "Al", f"S{i}", f"/p/A/Al/{i} S.m4a")


def index_args(**kw):
    values = {
        "plex_command": "index",
        "refresh": False,
        "resume": False,
        "batch_size": 500,
        "checkpoint_every": 3,
    }
    values.update(kw)
    return Namespace(**values)


def use_client(monkeypatch, fake):
    monkeypatch.setattr(plex_cli, "_settings", lambda _root: cli_settings())
    monkeypatch.setattr(plex_cli, "PlexClient", lambda _settings: fake)


def test_interrupted_index_resumes_without_duplicating_or_skipping(tmp_path, monkeypatch):
    use_client(monkeypatch, FakeIndexClient(10, interrupt_at=7))
    assert plex_cli.run(tmp_path, index_args()) == 130
    assert PlexIndex.load(cache_path(tmp_path)).next_start == 7

    use_client(monkeypatch, FakeIndexClient(10))
    assert plex_cli.run(tmp_path, index_args(resume=True)) == 0
    index = PlexIndex.load(cache_path(tmp_path))
    assert [t.rating_key for t in index.tracks] == [str(i) for i in range(10)]
    assert index.complete


def test_index_short_of_the_library_total_stays_incomplete(tmp_path, monkeypatch, capsys):
    use_client(monkeypatch, FakeIndexClient(8, total=10))
    assert plex_cli.run(tmp_path, index_args()) == 1
    assert PlexIndex.load(cache_path(tmp_path)).complete is False
    assert "--refresh" in capsys.readouterr().err


def test_resume_refuses_a_partial_index_from_another_server(tmp_path, monkeypatch):
    use_client(monkeypatch, FakeIndexClient(10, identifier="other", interrupt_at=4))
    plex_cli.run(tmp_path, index_args())

    use_client(monkeypatch, FakeIndexClient(10, identifier="abc"))
    with pytest.raises(SystemExit, match="server"):
        plex_cli.run(tmp_path, index_args(resume=True))


def test_cached_index_from_another_server_is_refused(tmp_path):
    PlexIndex(server_identifier="other", library="Music").save(cache_path(tmp_path))
    with pytest.raises(SystemExit, match="server"):
        plex_cli._load_index(tmp_path, cli_settings(), "abc")
    assert plex_cli._load_index(tmp_path, cli_settings(), "other").library == "Music"


def sync_args():
    return Namespace(
        plex_command="sync",
        playlist=None,
        apply=True,
        create_only=False,
        max_unresolved=5.0,
        max_ambiguous=2.0,
    )


def use_sync_client(monkeypatch, fake):
    use_client(monkeypatch, fake)
    monkeypatch.setattr(
        plex_cli, "_resolver", lambda _root, _settings, _client: PlexResolver(sample_tracks())
    )
    monkeypatch.setattr(
        plex_cli,
        "_manifests",
        lambda _root, _playlist: {"One": sample_entries(), "Two": sample_entries()},
    )


def test_a_request_error_fails_one_playlist_and_the_sync_continues(
    tmp_path, monkeypatch, capsys
):
    requests = pytest.importorskip("requests")
    client = FakePlexClient()
    create = client.create_playlist

    def create_or_fail(title, rating_keys):
        if title.startswith(f"{MANAGED_PREFIX}One"):
            raise requests.ConnectionError(f"http://h:32400/?X-Plex-Token={TOKEN}")
        return create(title, rating_keys)

    client.create_playlist = create_or_fail
    use_sync_client(monkeypatch, client)

    assert plex_cli.run(tmp_path, sync_args()) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    report = json.loads((tmp_path / "reports/plex-sync.json").read_text(encoding="utf-8"))
    assert TOKEN not in json.dumps(report)
    actions = {row["playlist"]: row["action"] for row in report["playlists"]}
    assert actions == {"One": "error", "Two": "create"}
    assert client.playlists_data[f"{MANAGED_PREFIX}Two"] == ["1", "2", "3", "4"]


def test_apply_exits_non_zero_when_verification_fails(tmp_path, monkeypatch):
    client = FakePlexClient()
    rename = client.rename_playlist

    def lossy_rename(title, new_title):
        rename(title, new_title)
        client.playlists_data[new_title] = client.playlists_data[new_title][:-1]
        return True

    client.rename_playlist = lossy_rename
    use_sync_client(monkeypatch, client)
    assert plex_cli.run(tmp_path, sync_args()) == 1
