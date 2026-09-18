"""Last.fm configuration, loaded from the environment.

Same rules as the Plex integration: the API key is read from an environment
variable, a gitignored `.env` may populate it, and a missing key fails
immediately with instructions rather than silently skipping. The key is never
logged, never written to a report, and never included in an error message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..plex.config import load_dotenv

ENV_API_KEY = "MUSIC_CURATOR_LASTFM_API_KEY"
ENV_RATE_LIMIT = "MUSIC_CURATOR_LASTFM_RATE_LIMIT"

API_ROOT = "https://ws.audioscrobbler.com/2.0/"

#: Last.fm documents a limit of 5 requests per second per originating IP. It is
#: the default and the ceiling for MUSIC_CURATOR_LASTFM_RATE_LIMIT.
DEFAULT_RATE_LIMIT = 5.0


class LastfmConfigurationError(RuntimeError):
    """Raised when the Last.fm API key is missing or unusable."""


def _redact(value: str) -> str:
    if not value:
        return "<unset>"
    return f"<set, {len(value)} chars>"


@dataclass(frozen=True)
class LastfmSettings:
    """Resolved Last.fm settings."""

    api_key: str = field(repr=False)
    rate_limit: float = DEFAULT_RATE_LIMIT
    timeout: float = 10.0

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"LastfmSettings(rate_limit={self.rate_limit!r}, "
            f"timeout={self.timeout!r}, api_key={_redact(self.api_key)})"
        )

    def describe(self) -> dict:
        """Loggable description. The key is never included."""
        return {
            "rate_limit": self.rate_limit,
            "timeout": self.timeout,
            "api_key": _redact(self.api_key),
        }

    @classmethod
    def from_env(
        cls, project_root: Path | None = None, environ: dict[str, str] | None = None
    ) -> LastfmSettings:
        env = dict(environ if environ is not None else os.environ)
        if project_root is not None:
            for key, value in load_dotenv(project_root / ".env").items():
                env.setdefault(key, value)  # real environment wins

        api_key = env.get(ENV_API_KEY, "").strip()
        if not api_key:
            raise LastfmConfigurationError(_missing_message(project_root))

        try:
            rate = float(env.get(ENV_RATE_LIMIT, "") or DEFAULT_RATE_LIMIT)
        except ValueError:
            rate = DEFAULT_RATE_LIMIT
        return cls(api_key=api_key, rate_limit=min(DEFAULT_RATE_LIMIT, max(0.5, rate)))


def _missing_message(project_root: Path | None) -> str:
    location = f"{project_root / '.env'}" if project_root else ".env"
    return "\n".join(
        [
            f"Last.fm is not configured. Missing required variable: {ENV_API_KEY}",
            "",
            "Create a free API account at https://www.last.fm/api/account/create",
            "then set the key in the environment, or in a gitignored .env at",
            f"  {location}",
            "",
            "Required:",
            f"  {ENV_API_KEY}=<your last.fm api key>",
            "",
            "Optional:",
            f"  {ENV_RATE_LIMIT}={DEFAULT_RATE_LIMIT}   (requests per second)",
            "",
            "The key is never written to logs, reports, or the repository.",
        ]
    )
