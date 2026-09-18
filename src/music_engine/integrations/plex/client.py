"""Thin wrapper around python-plexapi.

All Plex HTTP interaction goes through this module. Nothing else in the
codebase imports plexapi, so the rest of the engine stays testable without a
server and free of Plex concepts.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from .config import MANAGED_PREFIX, PlexConfigurationError, PlexSettings
from .models import PlexTrack

#: Rating keys sent per request when building a playlist. Plex puts them all in
#: the URL, so a large playlist must be batched or the request line exceeds the
#: server limit and the server responds 400.
PLAYLIST_BATCH_SIZE = 200


class PlexUnavailableError(RuntimeError):
    """Raised when the server cannot be reached or authentication fails."""


def request_errors() -> tuple[type[BaseException], ...]:
    """PlexUnavailableError plus the installed requests and plexapi base errors."""
    errors: list[type[BaseException]] = [PlexUnavailableError]
    try:
        import requests

        errors.append(requests.RequestException)
    except ImportError:  # pragma: no cover - depends on environment
        pass
    try:
        from plexapi.exceptions import PlexApiException

        errors.append(PlexApiException)
    except ImportError:  # pragma: no cover - depends on environment
        pass
    return tuple(errors)


def safe_reason(exc: BaseException) -> str:
    """Describe a request failure without its message.

    plexapi and requests messages can contain the request URL, and with it the
    token, so only the exception type is shown. A PlexUnavailableError message
    is built here and carries no secret.
    """
    if isinstance(exc, PlexUnavailableError):
        return str(exc)
    return type(exc).__name__


def _require_plexapi():
    try:
        from plexapi.server import PlexServer  # noqa: WPS433 (runtime import by design)
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise PlexConfigurationError(
            "python-plexapi is not installed. Install the optional dependency:\n"
            "  pip install -e '.[plex]'\n"
            "or run ./scripts/plex.sh, which installs it automatically."
        ) from exc
    return PlexServer


def _build_session(verify_ssl: bool):
    """Session honoring the SSL verification choice.

    With ``verify_ssl`` false the session accepts any certificate, and
    urllib3's InsecureRequestWarning is suppressed for the whole process.
    """
    import requests

    session = requests.Session()
    if not verify_ssl:
        session.verify = False
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:  # pragma: no cover - urllib3 always present with requests
            pass
    return session


class PlexClient:
    """Connection to a Plex server and its music library."""

    def __init__(self, settings: PlexSettings):
        self.settings = settings
        self._server = None

    # -- connection --------------------------------------------------------

    @property
    def server(self):
        if self._server is None:
            PlexServer = _require_plexapi()
            try:
                self._server = PlexServer(
                    self.settings.url,
                    self.settings.token,
                    session=_build_session(self.settings.verify_ssl),
                )
            except Exception as exc:
                # The token can appear in plexapi's exception text; never
                # propagate the raw message.
                raise PlexUnavailableError(
                    f"Could not connect to Plex at {self.settings.url}. "
                    f"Reason: {type(exc).__name__}. "
                    "Check the URL, the token, and whether "
                    "MUSIC_CURATOR_PLEX_VERIFY_SSL must be false for a "
                    "self-signed certificate."
                ) from None
        return self._server

    def status(self) -> dict:
        """Verify reachability, authentication, and library access."""
        server = self.server
        music_sections = [
            section.title
            for section in server.library.sections()
            if section.type == "artist"
        ]
        selected = self.settings.library
        available = selected in music_sections
        playlists = []
        if available:
            playlists = [p.title for p in server.playlists()]
        return {
            "reachable": True,
            "authenticated": True,
            "server_name": server.friendlyName,
            "plex_version": server.version,
            "machine_identifier": server.machineIdentifier,
            "music_libraries": music_sections,
            "selected_library": selected,
            "selected_library_available": available,
            "playlist_count": len(playlists),
            "managed_playlists": sorted(
                p for p in playlists if p.startswith(MANAGED_PREFIX)
            ),
            **{k: v for k, v in self.settings.describe().items() if k != "token"},
        }

    # -- library -----------------------------------------------------------

    def music_section(self):
        server = self.server
        try:
            section = server.library.section(self.settings.library)
        except Exception:
            available = [
                s.title for s in server.library.sections() if s.type == "artist"
            ]
            raise PlexUnavailableError(
                f"Music library {self.settings.library!r} not found. "
                f"Available music libraries: {available or 'none'}. "
                "Set MUSIC_CURATOR_PLEX_LIBRARY to one of these."
            ) from None
        if section.type != "artist":
            raise PlexUnavailableError(
                f"Library {self.settings.library!r} is a {section.type} library, "
                "not a music library."
            )
        return section

    def track_total(self) -> int:
        """Total track count, without fetching the tracks themselves."""
        section = self.music_section()
        try:
            return int(section.totalViewSize(libtype="track"))
        except Exception:  # pragma: no cover - depends on server version
            return 0

    def iter_tracks(
        self, start: int = 0, batch_size: int = 500
    ) -> Iterator[tuple[int, PlexTrack]]:
        """Yield (absolute_index, track) in pages.

        Fetching the whole library in one call takes hours on a large library
        and loses everything if interrupted. Paging lets the caller checkpoint
        progress and resume.
        """
        section = self.music_section()
        offset = start
        while True:
            page = section.search(
                libtype="track",
                container_start=offset,
                container_size=batch_size,
                maxresults=batch_size,
            )
            if not page:
                return
            for position, item in enumerate(page):
                yield offset + position, _to_plex_track(item)
            if len(page) < batch_size:
                return
            offset += len(page)

    # -- playlists ---------------------------------------------------------

    def section_playlists(self) -> list:
        """Playlists belonging to the selected music library only.

        `server.playlists()` spans every library on the server, so a
        same-named playlist in another music library would be read or
        overwritten. The fallback for a section without ``playlists()`` keeps
        only audio playlists whose ``librarySectionID`` equals the section key;
        a playlist that carries no ``librarySectionID`` is excluded.
        """
        section = self.music_section()
        try:
            return list(section.playlists())
        except (AttributeError, TypeError):  # pragma: no cover - older plexapi
            key = section.key
            scoped = []
            for candidate in self.server.playlists():
                if getattr(candidate, "playlistType", None) != "audio":
                    continue
                section_id = getattr(candidate, "librarySectionID", None)
                if section_id is not None and str(section_id) == str(key):
                    scoped.append(candidate)
            return scoped

    def playlist(self, title: str):
        for existing in self.section_playlists():
            if existing.title == title:
                return existing
        return None

    def playlist_items(self, title: str) -> list[str]:
        existing = self.playlist(title)
        if existing is None:
            return []
        return [str(item.ratingKey) for item in existing.items()]

    def _metadata_uri(self, rating_keys: Sequence[str]) -> str:
        """The server URI naming a set of library items."""
        return f"{self.server._uriRoot()}/library/metadata/{','.join(rating_keys)}"

    def create_playlist(
        self, title: str, rating_keys: list[str], batch_size: int = PLAYLIST_BATCH_SIZE
    ):
        """Create a playlist from rating keys, in batches, preserving order.

        The first batch creates the playlist and the rest are appended in
        order (see PLAYLIST_BATCH_SIZE). A failed append leaves the playlist
        partially populated; the caller removes it.

        Rating keys are posted directly rather than resolved into plexapi
        objects. `server.fetchItem` costs one HTTP round trip per track, which
        for a 24,000-track playlist would be 24,000 requests.
        """
        if not rating_keys:
            raise ValueError(
                f"Refusing to create empty playlist {title!r}: an empty playlist "
                "would replace real content with nothing."
            )

        from plexapi import utils
        from plexapi.playlist import Playlist

        server = self.server
        batch = max(1, batch_size)
        head, tail = rating_keys[:batch], rating_keys[batch:]

        args = {
            "uri": self._metadata_uri(head),
            "type": "audio",
            "title": title,
            "smart": 0,
        }
        key = f"/playlists{utils.joinArgs(args)}"
        data = server.query(key, method=server._session.post)[0]
        playlist = Playlist(server, data, initpath=key)

        for start in range(0, len(tail), batch):
            chunk = tail[start : start + batch]
            append_key = f"{playlist.key}/items{utils.joinArgs({'uri': self._metadata_uri(chunk)})}"
            server.query(append_key, method=server._session.put)

        return playlist

    def rename_playlist(self, title: str, new_title: str) -> bool:
        """Rename a playlist. Both titles must be managed."""
        for name in (title, new_title):
            if not name.startswith(MANAGED_PREFIX):
                raise ValueError(
                    f"Refusing to rename unmanaged playlist {name!r}. "
                    f"Music Curator only manages playlists prefixed {MANAGED_PREFIX!r}."
                )
        existing = self.playlist(title)
        if existing is None:
            return False
        existing.edit(title=new_title)
        return True

    def delete_playlist(self, title: str) -> bool:
        """Delete a playlist. Only ever called for managed titles."""
        if not title.startswith(MANAGED_PREFIX):
            raise ValueError(
                f"Refusing to delete unmanaged playlist {title!r}. "
                f"Music Curator only manages playlists prefixed {MANAGED_PREFIX!r}."
            )
        existing = self.playlist(title)
        if existing is None:
            return False
        existing.delete()
        return True


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _from_response_xml(item) -> PlexTrack | None:
    """Build a track from the search response, without touching plexapi lazily.

    Objects returned by a section search are partial, so reading an attribute
    such as ``item.media`` makes plexapi issue one HTTP reload per track. The
    response XML already holds every field, so it is read directly. Returns
    None if the element is not shaped as expected, and the caller falls back
    to attribute access.
    """
    data = getattr(item, "_data", None)
    if data is None or data.get("ratingKey") is None:
        return None

    media_path = ""
    for media in data.findall("Media"):
        for part in media.findall("Part"):
            if part.get("file"):
                media_path = part.get("file")
                break
        if media_path:
            break

    return PlexTrack(
        rating_key=str(data.get("ratingKey")),
        artist=data.get("grandparentTitle", "") or "",
        album_artist=data.get("grandparentTitle", "") or "",
        album=data.get("parentTitle", "") or "",
        title=data.get("title", "") or "",
        year=_int_or_none(data.get("year")),
        disc_number=_int_or_none(data.get("parentIndex")),
        track_number=_int_or_none(data.get("index")),
        duration=_int_or_none(data.get("duration")),
        media_path=media_path,
        guid=data.get("guid", "") or "",
        rating_count=int(data.get("ratingCount") or 0),
        view_count=int(data.get("viewCount") or 0),
    )


def _to_plex_track(item) -> PlexTrack:
    """Convert a plexapi track into our own model."""
    from_xml = _from_response_xml(item)
    if from_xml is not None:
        return from_xml

    # Fallback: attribute access, which may trigger a reload per track.
    media_path = ""
    try:
        for media in item.media:
            for part in media.parts:
                if part.file:
                    media_path = part.file
                    break
            if media_path:
                break
    except Exception:  # pragma: no cover - defensive against partial metadata
        media_path = ""

    return PlexTrack(
        rating_key=str(item.ratingKey),
        artist=getattr(item, "grandparentTitle", "") or "",
        album_artist=getattr(item, "grandparentTitle", "") or "",
        album=getattr(item, "parentTitle", "") or "",
        title=getattr(item, "title", "") or "",
        year=getattr(item, "year", None),
        disc_number=getattr(item, "parentIndex", None),
        track_number=getattr(item, "index", None),
        duration=getattr(item, "duration", None),
        media_path=media_path,
        guid=getattr(item, "guid", "") or "",
        rating_count=int(getattr(item, "ratingCount", 0) or 0),
        view_count=int(getattr(item, "viewCount", 0) or 0),
    )
