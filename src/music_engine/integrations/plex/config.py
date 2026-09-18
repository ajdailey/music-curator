"""Plex configuration, loaded from the environment.

Credentials are never stored in the repository, never hardcoded, and never
logged. The token is read from an environment variable; a gitignored `.env`
file is a convenience that populates those variables and nothing more.

If a required variable is missing, the Plex commands fail immediately naming
exactly what to set. They never fall back to a default, never prompt, and never
silently skip.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_URL = "MUSIC_CURATOR_PLEX_URL"
ENV_TOKEN = "MUSIC_CURATOR_PLEX_TOKEN"
ENV_LIBRARY = "MUSIC_CURATOR_PLEX_LIBRARY"
ENV_VERIFY_SSL = "MUSIC_CURATOR_PLEX_VERIFY_SSL"
ENV_MEDIA_ROOT = "MUSIC_CURATOR_PLEX_MEDIA_ROOT"

REQUIRED = (ENV_URL, ENV_TOKEN)
OPTIONAL = (ENV_LIBRARY, ENV_VERIFY_SSL, ENV_MEDIA_ROOT)

#: Playlists created by this tool carry this prefix. Anything without it is
#: not ours and is never modified or deleted.
MANAGED_PREFIX = "MC - "


class PlexConfigurationError(RuntimeError):
    """Raised when required Plex configuration is missing or unusable."""


def _redact(value: str) -> str:
    """Render a secret as ``<set>`` or ``<unset>``. No part of the value is shown."""
    return "<set>" if value else "<unset>"


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file into a plain mapping.

    Deliberately minimal: `KEY=value` lines, `#` comments, optional `export`
    prefix, and optional surrounding quotes. Values already present in the real
    environment always win, so an explicit shell variable overrides the file.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _as_bool(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PlexSettings:
    """Resolved Plex connection settings."""

    url: str
    token: str = field(repr=False)  # never rendered by the default repr
    library: str = "Music"
    verify_ssl: bool = True
    media_root: str = "/music_portable"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"PlexSettings(url={self.url!r}, library={self.library!r}, "
            f"verify_ssl={self.verify_ssl!r}, media_root={self.media_root!r}, "
            f"token={_redact(self.token)})"
        )

    def describe(self) -> dict:
        """Loggable description. The token is never included."""
        return {
            "url": self.url,
            "library": self.library,
            "verify_ssl": self.verify_ssl,
            "media_root": self.media_root,
            "token": _redact(self.token),
        }

    @classmethod
    def from_env(
        cls, project_root: Path | None = None, environ: dict[str, str] | None = None
    ) -> PlexSettings:
        """Build settings from the environment, with .env as a fallback source.

        Raises PlexConfigurationError naming every missing variable at once, so
        the user fixes the whole problem in one pass rather than one variable
        per run.
        """
        env = dict(environ if environ is not None else os.environ)
        if project_root is not None:
            for key, value in load_dotenv(project_root / ".env").items():
                env.setdefault(key, value)  # real environment wins

        missing = [name for name in REQUIRED if not env.get(name, "").strip()]
        if missing:
            raise PlexConfigurationError(_missing_message(missing, project_root))

        url = env[ENV_URL].strip().rstrip("/")
        verify_default = not url.startswith("https://")
        return cls(
            url=url,
            token=env[ENV_TOKEN].strip(),
            library=env.get(ENV_LIBRARY, "").strip() or "Music",
            # A Plex server on a private address almost always presents a
            # self-signed certificate, so an https URL defaults to no
            # certificate verification. Set MUSIC_CURATOR_PLEX_VERIFY_SSL=true
            # to verify.
            verify_ssl=_as_bool(env.get(ENV_VERIFY_SSL, ""), verify_default),
            media_root=env.get(ENV_MEDIA_ROOT, "").strip() or "/music_portable",
        )


def _missing_message(missing: list[str], project_root: Path | None) -> str:
    location = f"{project_root / '.env'}" if project_root else ".env"
    lines = [
        "Plex configuration is incomplete. Missing required variable(s): "
        + ", ".join(missing),
        "",
        "Set them in the environment, or create a gitignored .env file at",
        f"  {location}",
        "",
        "Required:",
        f"  {ENV_URL}=https://192.168.1.10:32400",
        f"  {ENV_TOKEN}=<your plex token>",
        "",
        "Optional:",
        f"  {ENV_LIBRARY}=Music            (default: Music)",
        f"  {ENV_VERIFY_SSL}=false   (default: false for https, true otherwise)",
        f"  {ENV_MEDIA_ROOT}=/music_portable",
        "",
        "The token is never written to logs, reports, or the repository.",
    ]
    return "\n".join(lines)
