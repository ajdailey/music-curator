from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import yaml

from .identity import IdentityResolver


@dataclass(frozen=True)
class Track:
    artist: str
    album: str
    title: str
    source_path: str
    relative_path: str
    flags: frozenset[str]
    track_no: int | None = None
    disc_no: int | None = None
    #: Version kinds an album override rules out. ``versions.classify`` skips
    #: them in flag and text detection alike.
    cleared_flags: frozenset[str] = frozenset()

    @property
    def key(self) -> str:
        return normalize_relative_path(self.relative_path)


def normalize_relative_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("/").casefold()


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def canonical_tags(tag_config: Mapping) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return tag -> ancestors (including self), plus alias -> canonical tag."""
    nodes = tag_config.get("tags", {})
    aliases = {str(k).casefold(): str(v) for k, v in tag_config.get("aliases", {}).items()}
    parents: dict[str, list[str]] = {}
    for name, data in nodes.items():
        parents[str(name)] = [str(v) for v in (data or {}).get("parents", [])]

    memo: dict[str, set[str]] = {}

    def expand(name: str, stack: frozenset[str] = frozenset()) -> set[str]:
        canonical = aliases.get(name.casefold(), name)
        if canonical in memo:
            return memo[canonical]
        if canonical in stack:
            raise ValueError(f"Taxonomy cycle detected at {canonical}")
        result = {canonical}
        for parent in parents.get(canonical, []):
            result.update(expand(parent, stack | {canonical}))
        memo[canonical] = result
        return result

    for name in nodes:
        expand(str(name))
    return memo, aliases


def artist_profiles(
    artist_config: Mapping,
    tag_config: Mapping,
    resolver: IdentityResolver | None = None,
) -> dict[str, dict]:
    """Curated artist classifications, keyed by canonical artist identity.

    Keying by identity (rather than by raw case-folded name) is what allows a
    profile written as ``R.E.M.`` to apply to the catalog's ``R.E.M_`` rows.
    """
    closure, aliases = canonical_tags(tag_config)
    profiles: dict[str, dict] = {}
    for name, raw in artist_config.get("artists", {}).items():
        if not isinstance(name, str):
            raise ValueError(
                f"Artist key {name!r} is not a string. Quote YAML names "
                "such as 'Yes', 'No', 'On', and 'Off'."
            )
        data = raw or {}
        explicit = [str(v) for v in data.get("tags", data.get("genres", []))]
        expanded: set[str] = set()
        for tag in explicit:
            canonical = aliases.get(tag.casefold(), tag)
            expanded.update(closure.get(canonical, {canonical}))
        identity = resolver.resolve(name) if resolver else name
        profiles[identity] = {
            **data,
            "name": name,
            "identity": identity,
            "tags": expanded,
            "explicit_tags": set(explicit),
        }
    return profiles


def cornerstone_artists(profiles: Mapping[str, dict]) -> set[str]:
    """Identities explicitly marked ``importance: cornerstone``."""
    return {
        identity
        for identity, profile in profiles.items()
        if str(profile.get("importance", "")).casefold() == "cornerstone"
    }


def playlist_path_keys(path: Path, windows_root: str) -> set[str]:
    """Read an M3U/M3U8 and normalize entries to catalog-relative keys."""
    if not path.exists():
        raise FileNotFoundError(f"Source playlist not found: {path}")
    root_norm = windows_root.replace("/", "\\").rstrip("\\") + "\\"
    root_fold = root_norm.casefold()
    keys: set[str] = set()
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for raw in f:
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            win = value.replace("/", "\\")
            if win.casefold().startswith(root_fold):
                rel = win[len(root_norm):]
            else:
                rel = str(PureWindowsPath(win))
                if PureWindowsPath(win).is_absolute():
                    # Absolute path under another mount cannot be mapped safely.
                    continue
            keys.add(normalize_relative_path(rel))
    return keys


class PlaylistMatcher:
    """A playlist definition compiled against the identity system.

    Membership is decided in two stages because every criterion except
    ``exclude_flags`` is artist-level. Deciding per artist (thousands) instead
    of per track (hundreds of thousands) keeps generation linear in the catalog.
    """

    def __init__(
        self,
        definition: Mapping,
        profiles: Mapping[str, dict],
        resolver: IdentityResolver,
        album_profiles=None,
        tag_aliases: Mapping[str, str] | None = None,
    ) -> None:
        self.definition = definition
        self.name = str(definition.get("name", "unnamed"))
        self.profiles = profiles
        self.resolver = resolver
        self.album_profiles = album_profiles
        self.include_related = bool(definition.get("include_related", False))
        self.includes = resolver.expand(
            [str(x) for x in definition.get("include_artists", [])],
            include_related=self.include_related,
        )
        self.excludes = resolver.expand(
            [str(x) for x in definition.get("exclude_artists", [])]
        )
        aliases = tag_aliases or {}

        def canonical(tags) -> set[str]:
            return {aliases.get(str(t).casefold(), str(t)) for t in tags}

        self.required_tags = canonical(definition.get("match_all_tags", []))
        self.desired_tags = canonical(
            definition.get("match_any_tags", definition.get("match_any_genres", []))
        )
        self.exclude_flags = set(definition.get("exclude_flags", []))
        # Keyed by (identity, album) because album profiles can add tags that
        # the artist does not carry. Distinct pairs number in the tens of
        # thousands, so this still avoids per-track evaluation of the catalog.
        self._cache: dict[tuple[str, str], bool] = {}

    def effective_tags(self, identity: str, album: str) -> set[str]:
        """Artist tags minus the album's ``clear_tags``, plus the album's tags."""
        tags = set(self.profiles.get(identity, {}).get("tags", set()))
        if self.album_profiles is not None and album:
            tags -= self.album_profiles.cleared_tags_for(identity, album)
            tags |= self.album_profiles.tags_for(identity, album)
        return tags

    def artist_allows(self, identity: str, album: str = "") -> bool:
        key = (identity, album)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._evaluate(identity, album)
        self._cache[key] = result
        return result

    def _evaluate(self, identity: str, album: str) -> bool:
        if identity in self.excludes:
            return False
        tags = self.effective_tags(identity, album)
        artist_match = identity in self.includes
        tag_match = bool(self.desired_tags and (tags & self.desired_tags))
        unrestricted = not self.includes and not self.desired_tags
        all_match = self.required_tags.issubset(tags)
        return (artist_match or tag_match or unrestricted) and all_match

    def track_allows(self, track: Track) -> bool:
        return not (track.flags & self.exclude_flags)

    def select(self, tracks: Iterable[Track]) -> list[Track]:
        resolve = self.resolver.resolve
        return [
            t
            for t in tracks
            if self.artist_allows(resolve(t.artist), t.album) and self.track_allows(t)
        ]


def matches(
    track: Track,
    definition: Mapping,
    profiles: Mapping[str, dict],
    resolver: IdentityResolver | None = None,
    tag_aliases: Mapping[str, str] | None = None,
) -> bool:
    """Single-track membership test (convenience wrapper around PlaylistMatcher)."""
    resolver = resolver or IdentityResolver()
    matcher = PlaylistMatcher(definition, profiles, resolver, tag_aliases=tag_aliases)
    return matcher.artist_allows(
        resolver.resolve(track.artist), track.album
    ) and matcher.track_allows(track)


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', " - ", name).strip()


def write_m3u(path: Path, tracks: Iterable[Track], root: str) -> int:
    """Write an M3U8 playlist.

    An empty playlist is never written: the file is removed if it exists so a
    previous run's output cannot be mistaken for current results.
    """
    rows = list(tracks)
    if not rows:
        if path.exists():
            path.unlink()
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        f.write(f"#PLAYLIST:{path.stem}\n")
        for t in rows:
            f.write(f"#EXTINF:-1,{t.artist} - {t.title}\n")
            f.write(root.rstrip("/") + "/" + t.relative_path.replace("\\", "/") + "\n")
    return len(rows)
