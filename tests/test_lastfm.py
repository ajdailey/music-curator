"""Last.fm enrichment: configuration safety, caching, and rate limiting."""

from __future__ import annotations

import json
from argparse import Namespace

import pytest

from music_engine import lastfm_cli
from music_engine.integrations.lastfm.client import (
    DEFAULT_CACHE_PATH,
    LastfmAuthError,
    LastfmClient,
    LastfmRequestError,
    LookupCache,
)
from music_engine.integrations.lastfm.config import (
    ENV_API_KEY,
    ENV_RATE_LIMIT,
    LastfmConfigurationError,
    LastfmSettings,
)

KEY = "lastfmsecretkey123"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.calls = payload, status, []

    def get(self, url, params, timeout):
        self.calls.append(params)
        return FakeResponse(self.payload, self.status)


class TimeoutSession:
    def get(self, url, params, timeout):
        raise TimeoutError(f"{url}?api_key={params['api_key']}")


def settings(**kw):
    return LastfmSettings(api_key=KEY, **kw)


# --- configuration --------------------------------------------------------


def test_missing_key_fails_with_instructions(tmp_path):
    with pytest.raises(LastfmConfigurationError) as exc:
        LastfmSettings.from_env(tmp_path, environ={})
    message = str(exc.value)
    assert ENV_API_KEY in message
    assert "last.fm/api/account/create" in message


def test_key_is_never_shown(tmp_path):
    resolved = LastfmSettings.from_env(tmp_path, environ={ENV_API_KEY: KEY})
    assert KEY not in repr(resolved)
    assert KEY not in json.dumps(resolved.describe())


def test_key_can_come_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text(f"{ENV_API_KEY}={KEY}\n", encoding="utf-8")
    assert LastfmSettings.from_env(tmp_path, environ={}).api_key == KEY


def test_rate_limit_is_configurable_and_floored(tmp_path):
    fast = LastfmSettings.from_env(
        tmp_path, environ={ENV_API_KEY: KEY, ENV_RATE_LIMIT: "3"}
    )
    assert fast.rate_limit == 3.0
    floored = LastfmSettings.from_env(
        tmp_path, environ={ENV_API_KEY: KEY, ENV_RATE_LIMIT: "0.01"}
    )
    assert floored.rate_limit >= 0.5


def test_rate_limit_is_capped_at_the_documented_five_per_second(tmp_path):
    capped = LastfmSettings.from_env(
        tmp_path, environ={ENV_API_KEY: KEY, ENV_RATE_LIMIT: "50"}
    )
    assert capped.rate_limit == 5.0


# --- lookups --------------------------------------------------------------


def test_parses_listener_and_play_counts():
    session = FakeSession({"track": {"listeners": "704161", "playcount": "5100000"}})
    client = LastfmClient(settings(), session=session, sleep=lambda _s: None)
    info = client.track_info("Stone Temple Pilots", "Interstate Love Song")
    assert info == {"listeners": 704161, "playcount": 5100000}


def test_unknown_track_returns_none():
    session = FakeSession({"error": 6, "message": "Track not found"})
    client = LastfmClient(settings(), session=session, sleep=lambda _s: None)
    assert client.track_info("Nobody", "Nothing") is None


@pytest.mark.parametrize(
    "session",
    [
        FakeSession({}, status=500),
        FakeSession(None),
        FakeSession({"error": 29, "message": "Rate limit exceeded"}, status=429),
        TimeoutSession(),
    ],
    ids=["http-500", "malformed-json", "rate-limit", "timeout"],
)
def test_failed_request_raises_without_the_key(session):
    client = LastfmClient(settings(), session=session, sleep=lambda _s: None)
    with pytest.raises(LastfmRequestError) as exc:
        client.track_info("A", "B")
    assert KEY not in str(exc.value)


def test_rejected_key_raises_an_auth_error():
    session = FakeSession({"error": 10, "message": "Invalid API key"}, status=403)
    client = LastfmClient(settings(), session=session, sleep=lambda _s: None)
    with pytest.raises(LastfmAuthError):
        client.track_info("A", "B")


def test_blank_artist_or_title_makes_no_request():
    session = FakeSession({"track": {"listeners": "1"}})
    client = LastfmClient(settings(), session=session, sleep=lambda _s: None)
    assert client.track_info("", "Song") is None
    assert client.track_info("Artist", "") is None
    assert session.calls == []


def test_api_key_is_sent_but_request_is_rate_limited():
    slept = []
    session = FakeSession({"track": {"listeners": "10", "playcount": "1"}})
    client = LastfmClient(settings(rate_limit=5.0), session=session, sleep=slept.append)
    client.track_info("A", "B")
    client.track_info("C", "D")
    assert session.calls[0]["api_key"] == KEY
    assert client.requests_made == 2
    assert slept, "second request should have been throttled"


# --- cache ----------------------------------------------------------------


def test_cache_round_trips_hits_and_misses(tmp_path):
    cache = LookupCache()
    cache.put("a|hit", 100, 5, found=True)
    cache.put("a|miss", 0, 0, found=False)
    path = tmp_path / "lastfm.json"
    cache.save(path)

    loaded = LookupCache.load(path)
    assert loaded.get("a|hit")["listeners"] == 100
    assert loaded.get("a|miss")["found"] is False
    assert loaded.get("a|absent") is None


def test_schema_1_cache_keeps_only_its_hits(tmp_path):
    path = tmp_path / "lastfm.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": {
                    "a|hit": {"listeners": 100, "playcount": 5, "found": True},
                    "a|miss": {"listeners": 0, "playcount": 0, "found": False},
                },
            }
        ),
        encoding="utf-8",
    )
    assert list(LookupCache.load(path).entries) == ["a|hit"]


def test_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "lastfm.json"
    path.write_text("{not json", encoding="utf-8")
    assert LookupCache.load(path).entries == {}


def test_missing_cache_starts_empty(tmp_path):
    assert LookupCache.load(tmp_path / "absent.json").entries == {}


# --- enrich ---------------------------------------------------------------


class FakeTrack:
    def __init__(self, artist, title):
        self.artist, self.title = artist, title


class FakeProject:
    catalog = [FakeTrack("Artist", "Found"), FakeTrack("Artist", "Missing")]
    identity_of = {"Artist": "Artist"}

    @classmethod
    def load(cls, _root):
        return cls()


class RoutedSession:
    """Answers per track title: a payload, or an exception to raise."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, params, timeout):
        track = params["track"].casefold()
        self.calls.append(track)
        answer = self.answers[track]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(*answer)


def run_enrich(tmp_path, monkeypatch, session):
    monkeypatch.setenv(ENV_API_KEY, KEY)
    monkeypatch.setattr(lastfm_cli, "Project", FakeProject)
    monkeypatch.setattr(
        lastfm_cli,
        "LastfmClient",
        lambda resolved: LastfmClient(resolved, session=session, sleep=lambda _s: None),
    )
    args = Namespace(lastfm_command="enrich", limit=0, dry_run=False)
    return lastfm_cli.run(tmp_path, args)


FOUND = ({"track": {"listeners": "10", "playcount": "20"}}, 200)
NOT_FOUND = ({"error": 6, "message": "Track not found"}, 200)


def test_only_a_not_found_answer_is_cached_as_a_miss(tmp_path, monkeypatch):
    session = RoutedSession({"found": FOUND, "missing": NOT_FOUND})
    assert run_enrich(tmp_path, monkeypatch, session) == 0
    entries = LookupCache.load(tmp_path / DEFAULT_CACHE_PATH).entries
    assert sorted(e["found"] for e in entries.values()) == [False, True]


def test_a_transient_failure_is_not_cached_and_exits_non_zero(tmp_path, monkeypatch):
    session = RoutedSession({"found": FOUND, "missing": TimeoutError("down")})
    assert run_enrich(tmp_path, monkeypatch, session) == 1
    entries = LookupCache.load(tmp_path / DEFAULT_CACHE_PATH).entries
    assert [e["found"] for e in entries.values()] == [True]

    session = RoutedSession({"found": FOUND, "missing": NOT_FOUND})
    assert run_enrich(tmp_path, monkeypatch, session) == 0
    assert session.calls == ["missing"]


def test_a_rejected_key_stops_the_run(tmp_path, monkeypatch, capsys):
    rejected = ({"error": 10, "message": "Invalid API key"}, 403)
    session = RoutedSession({"found": rejected, "missing": FOUND})
    assert run_enrich(tmp_path, monkeypatch, session) == 2
    assert len(session.calls) == 1
    assert KEY not in capsys.readouterr().err
    assert LookupCache.load(tmp_path / DEFAULT_CACHE_PATH).entries == {}
