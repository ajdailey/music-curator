"""Last.fm track lookups.

Only `track.getInfo` is used, which returns global `listeners` and `playcount`
for an artist/title pair. Requests are rate limited to Last.fm's published
limit. The caller caches a hit and a "track not found" answer; a failed request
raises and is not cached, so a rerun retries it.

The artist and title are sent to Last.fm. They are public music metadata, but
this is an external network call and only ever happens on explicit request.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import API_ROOT, LastfmSettings

#: A schema 1 cache can record a failed request as not found, so loading one
#: keeps only its hits.
SCHEMA_VERSION = 2
DEFAULT_CACHE_PATH = ".cache/lastfm.json"

#: The error code Last.fm returns for an unknown artist/title pair.
TRACK_NOT_FOUND = 6
#: Authentication failed, invalid session key, invalid API key, suspended key.
AUTH_ERRORS = frozenset({4, 9, 10, 26})


class LastfmRequestError(RuntimeError):
    """A lookup failed for a reason a retry can fix: network, HTTP, rate limit.

    The message never contains the request URL, which carries the API key.
    """


class LastfmAuthError(RuntimeError):
    """Last.fm rejected the API key."""


@dataclass
class LookupCache:
    """Cached Last.fm answers: hits and "track not found"."""

    entries: dict[str, dict] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, listeners: int, playcount: int, found: bool) -> None:
        self.entries[key] = {
            "listeners": listeners,
            "playcount": playcount,
            "found": found,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": self.schema_version, "entries": self.entries},
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> LookupCache:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        version = int(data.get("schema_version", 0))
        entries = dict(data.get("entries", {}))
        if version == 1:
            entries = {k: v for k, v in entries.items() if v.get("found")}
        elif version != SCHEMA_VERSION:
            return cls()
        return cls(entries=entries)


class LastfmClient:
    """Rate-limited Last.fm API client."""

    def __init__(self, settings: LastfmSettings, session=None, sleep=time.sleep):
        self.settings = settings
        self._session = session
        self._sleep = sleep
        self._last_request = 0.0
        self.requests_made = 0

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _throttle(self) -> None:
        interval = 1.0 / max(self.settings.rate_limit, 0.1)
        elapsed = time.monotonic() - self._last_request
        if elapsed < interval:
            self._sleep(interval - elapsed)
        self._last_request = time.monotonic()

    def track_info(self, artist: str, title: str) -> dict | None:
        """Return {'listeners', 'playcount'}, or None when Last.fm has no match.

        Raises LastfmAuthError when the key is rejected and LastfmRequestError
        for any other failure.
        """
        if not artist or not title:
            return None
        self._throttle()
        self.requests_made += 1
        try:
            response = self.session.get(
                API_ROOT,
                params={
                    "method": "track.getInfo",
                    "api_key": self.settings.api_key,
                    "artist": artist,
                    "track": title,
                    "format": "json",
                    "autocorrect": "1",
                },
                timeout=self.settings.timeout,
            )
        except Exception as exc:
            # The exception text can contain the URL and therefore the key.
            raise LastfmRequestError(type(exc).__name__) from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and "error" in payload:
            try:
                code = int(payload["error"])
            except (TypeError, ValueError):
                code = None
            if code == TRACK_NOT_FOUND:
                return None
            if code in AUTH_ERRORS:
                raise LastfmAuthError(f"Last.fm rejected the API key (error {code}).")
            raise LastfmRequestError(f"Last.fm error {code}")
        if response.status_code != 200:
            raise LastfmRequestError(f"HTTP {response.status_code}")
        track = payload.get("track") if isinstance(payload, dict) else None
        if not isinstance(track, dict):
            raise LastfmRequestError("unexpected response")
        try:
            return {
                "listeners": int(track.get("listeners", 0) or 0),
                "playcount": int(track.get("playcount", 0) or 0),
            }
        except (TypeError, ValueError):
            raise LastfmRequestError("unexpected response") from None
