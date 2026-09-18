"""Album-level knowledge.

Artist profiles cannot express everything that matters. `Nebraska` and
`Born in the U.S.A.` are the same artist and a different musical identity, and
an artist's official bootleg series is a different kind of recording from their
studio catalog even though the artist tag is identical.

This module adds a layer between artist and track:

* **pattern rules** match many albums at once (a dated concert recording), and
* **explicit entries** carry curated per-album knowledge.

Both can contribute tags (affecting playlist membership) and flags (affecting
version classification). An explicit entry can also remove them: ``clear_tags``
drops tags from the artist's closure for that album, and ``clear_flags`` rules
out version kinds that catalog flags or album and title text would otherwise
assign. Explicit entries win over patterns.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .core import Track
from .identity import IdentityResolver
from .versions import KNOWN_FLAGS

# The library export truncates every path segment at 40 characters, so
# "Automatic For The People (25th Anniversary Edition)" reaches the catalog as
# "Automatic For The People (25th Anniversa" with an unclosed parenthesis.
_TRUNCATED_TAIL = re.compile(r"\s*[\(\[][^\)\]]*$")

# One album exists in many editions. These markers are stripped so a single
# declared entry covers every edition of the record.
_EDITION_MARKERS = (
    r"deluxe|remaster(?:ed)?|edition|anniversary|hi[-_]?res|version|expanded|"
    r"mono|stereo|legacy|bonus|reissue|collector|disc\s*\d+|\d{2}[-_/]\d{3}"
)
_EDITION_PARENTHETICAL = re.compile(
    rf"\s*[\(\[][^\)\]]*(?:{_EDITION_MARKERS})[^\)\]]*[\)\]]", re.IGNORECASE
)
_EDITION_SUFFIX = re.compile(rf"\s+-\s+(?:{_EDITION_MARKERS}).*$", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def base_album(album: str) -> str:
    """Normalize an album name so every edition of a record shares one key.

    Handles both the 40-character truncation and the edition suffixes:

    * ``Murmur - Deluxe Edition``                 -> ``murmur``
    * ``Kind Of Blue (Legacy Edition)``           -> ``kind of blue``
    * ``Automatic For The People (25th Anniversa`` -> ``automatic for the people``

    Deliberately conservative: it strips edition wrappers, never words.
    ``Harvest`` and ``Harvest Moon`` stay distinct records, and
    ``Nebraska Outtakes`` does not collapse into ``Nebraska``.
    """
    value = _EDITION_PARENTHETICAL.sub("", album)
    value = _TRUNCATED_TAIL.sub("", value)
    value = _EDITION_SUFFIX.sub("", value)
    value = _WHITESPACE.sub(" ", value).strip(" -_")
    return (value or album).casefold()


@dataclass(frozen=True)
class AlbumPattern:
    """A rule matching many albums by name."""

    name: str
    pattern: re.Pattern[str]
    flags: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    note: str = ""

    def matches(self, album: str) -> bool:
        return bool(self.pattern.search(album))


@dataclass(frozen=True)
class AlbumEntry:
    """Curated knowledge about one specific album."""

    artist: str
    album: str
    tags: frozenset[str] = frozenset()
    flags: frozenset[str] = frozenset()
    clear_tags: frozenset[str] = frozenset()
    clear_flags: frozenset[str] = frozenset()
    masterpiece: bool = False
    complete_album: bool = False
    year: int | None = None
    note: str = ""
    matches: re.Pattern[str] | None = None


def _album_key(artist: str, album: str) -> tuple[str, str]:
    return (artist.casefold(), base_album(album))


@dataclass
class AlbumProfiles:
    """Album-level tags, flags, and attributes."""

    patterns: list[AlbumPattern] = field(default_factory=list)
    entries: dict[tuple[str, str], AlbumEntry] = field(default_factory=dict)
    regex_entries: dict[str, list[AlbumEntry]] = field(default_factory=dict)
    _pattern_cache: dict[str, tuple[frozenset[str], frozenset[str]]] = field(
        default_factory=dict, repr=False
    )

    @classmethod
    def from_config(
        cls,
        config: Mapping,
        resolver: IdentityResolver | None = None,
        tag_aliases: Mapping[str, str] | None = None,
        tag_closure: Mapping[str, set[str]] | None = None,
    ) -> AlbumProfiles:
        """Load album knowledge.

        Entry artist keys resolve through ``resolver``, so ``R.E.M.`` applies to
        the catalog's ``R.E.M_`` rows. Tag names resolve through
        ``tag_aliases``. Pattern and entry ``tags`` expand through
        ``tag_closure`` (tag -> ancestors, as ``canonical_tags`` returns it), so
        an album tagged ``folk-rock`` also matches a playlist on ``rock``.
        ``clear_tags`` does not expand: clearing ``heartland-rock`` leaves
        ``rock`` in place.
        """
        profiles = cls()
        aliases = tag_aliases or {}
        closure = tag_closure or {}

        def canonical_set(values) -> frozenset[str]:
            return frozenset(aliases.get(str(t).casefold(), str(t)) for t in values or [])

        def tag_set(values) -> frozenset[str]:
            expanded: set[str] = set()
            for tag in canonical_set(values):
                expanded.update(closure.get(tag, {tag}))
            return frozenset(expanded)

        for raw in config.get("patterns") or []:
            data = raw or {}
            expression = str(data.get("album_matches", ""))
            if not expression:
                raise ValueError(
                    f"Album pattern {data.get('name', '<unnamed>')!r} has no 'album_matches'"
                )
            profiles.patterns.append(
                AlbumPattern(
                    name=str(data.get("name", expression)),
                    pattern=re.compile(expression),
                    flags=frozenset(str(f) for f in data.get("flags", [])),
                    tags=tag_set(data.get("tags")),
                    note=str(data.get("note", "")),
                )
            )

        for artist, albums in (config.get("albums") or {}).items():
            if not isinstance(artist, str):
                raise ValueError(f"Album profile artist key {artist!r} is not a string")
            for album, raw in (albums or {}).items():
                if not isinstance(album, str):
                    raise ValueError(
                        f"Album profile key {album!r} under {artist!r} is not a string. "
                        "Quote YAML names such as 'Yes', 'No', and dates."
                    )
                data = raw or {}
                expression = data.get("album_matches")
                entry = AlbumEntry(
                    artist=artist,
                    album=album,
                    matches=re.compile(str(expression)) if expression else None,
                    tags=tag_set(data.get("tags")),
                    flags=frozenset(str(f) for f in data.get("flags", [])),
                    clear_tags=canonical_set(data.get("clear_tags")),
                    clear_flags=frozenset(str(f) for f in data.get("clear_flags") or []),
                    masterpiece=bool(data.get("masterpiece", False)),
                    complete_album=bool(data.get("complete_album", False)),
                    year=int(data["year"]) if data.get("year") is not None else None,
                    note=str(data.get("note", "")),
                )
                identity = resolver.resolve(artist) if resolver else artist
                profiles.entries[_album_key(identity, album)] = entry
                if entry.matches is not None:
                    profiles.regex_entries.setdefault(identity.casefold(), []).append(entry)
        return profiles

    def errors(self, declared_tags: Iterable[str]) -> list[str]:
        """Unknown names in ``clear_tags`` and ``clear_flags``."""
        known_tags = set(declared_tags)
        problems: list[str] = []
        for entry in self.entries.values():
            label = f"Album '{entry.artist} - {entry.album}'"
            problems.extend(
                f"{label} clears undeclared tag '{tag}'"
                for tag in sorted(entry.clear_tags - known_tags)
            )
            problems.extend(
                f"{label} clears unknown flag '{flag}'; expected one of "
                f"{', '.join(sorted(KNOWN_FLAGS))}"
                for flag in sorted(entry.clear_flags - KNOWN_FLAGS)
            )
        return problems

    # -- lookups -----------------------------------------------------------

    def _from_patterns(self, album: str) -> tuple[frozenset[str], frozenset[str]]:
        """(flags, tags) contributed by pattern rules, cached per album name."""
        cached = self._pattern_cache.get(album)
        if cached is not None:
            return cached
        flags: set[str] = set()
        tags: set[str] = set()
        for rule in self.patterns:
            if rule.matches(album):
                flags.update(rule.flags)
                tags.update(rule.tags)
        result = (frozenset(flags), frozenset(tags))
        self._pattern_cache[album] = result
        return result

    def entry_for(self, identity: str, album: str) -> AlbumEntry | None:
        """Find the entry for an album, tolerating editions and truncation."""
        entry = self.entries.get(_album_key(identity, album))
        if entry is not None:
            return entry
        for candidate in self.regex_entries.get(identity.casefold(), []):
            if candidate.matches is not None and candidate.matches.search(album):
                return candidate
        return None

    def flags_for(self, identity: str, album: str) -> frozenset[str]:
        pattern_flags, _ = self._from_patterns(album)
        entry = self.entry_for(identity, album)
        if entry is None:
            return pattern_flags
        return pattern_flags | entry.flags

    def cleared_flags_for(self, identity: str, album: str) -> frozenset[str]:
        entry = self.entry_for(identity, album)
        return entry.clear_flags if entry is not None else frozenset()

    def cleared_tags_for(self, identity: str, album: str) -> frozenset[str]:
        entry = self.entry_for(identity, album)
        return entry.clear_tags if entry is not None else frozenset()

    def tags_for(self, identity: str, album: str) -> frozenset[str]:
        _, pattern_tags = self._from_patterns(album)
        entry = self.entry_for(identity, album)
        if entry is None:
            return pattern_tags
        return pattern_tags | entry.tags

    def matched_patterns(self, album: str) -> list[str]:
        return [rule.name for rule in self.patterns if rule.matches(album)]

    # -- application -------------------------------------------------------

    def apply_flags(
        self, tracks: Iterable[Track], identity_of: Mapping[str, str] | None = None
    ) -> tuple[list[Track], int]:
        """Return tracks with album-derived flags merged in and cleared flags removed.

        A cleared flag is also recorded on ``Track.cleared_flags`` so version
        classification ignores text that names it. Tracks are frozen, so a
        changed track is replaced. Returns the new list and the number of tracks
        actually changed, which the report uses to show how much album knowledge
        is doing.
        """
        resolve = (identity_of or {}).get
        updated: list[Track] = []
        changed = 0
        for track in tracks:
            identity = resolve(track.artist, track.artist)
            cleared = self.cleared_flags_for(identity, track.album)
            flags = (track.flags | self.flags_for(identity, track.album)) - cleared
            if flags != track.flags or not cleared <= track.cleared_flags:
                updated.append(
                    dataclasses.replace(
                        track, flags=flags, cleared_flags=track.cleared_flags | cleared
                    )
                )
                changed += 1
            else:
                updated.append(track)
        return updated, changed

    def statistics(
        self, tracks: Iterable[Track], identity_of: Mapping[str, str] | None = None
    ) -> dict:
        """Coverage of album knowledge, for reporting."""
        resolve = (identity_of or {}).get
        by_pattern: dict[str, int] = {rule.name: 0 for rule in self.patterns}
        explicit = 0
        albums: set[tuple[str, str]] = set()
        for track in tracks:
            identity = resolve(track.artist, track.artist)
            albums.add(_album_key(identity, track.album))
            for name in self.matched_patterns(track.album):
                by_pattern[name] += 1
            if self.entry_for(identity, track.album) is not None:
                explicit += 1
        return {
            "declared_patterns": len(self.patterns),
            "declared_albums": len(self.entries),
            "catalog_albums": len(albums),
            "tracks_matched_by_pattern": by_pattern,
            "tracks_matched_by_explicit_entry": explicit,
            "declared_albums_absent_from_catalog": sorted(
                f"{entry.artist} - {entry.album}"
                for key, entry in self.entries.items()
                if key not in albums
            ),
        }
