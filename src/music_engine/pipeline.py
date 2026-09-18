"""Project loading and the generation pipeline.

Pipeline order: source scope -> taxonomy matching -> candidate balancing ->
sequencing -> output -> reporting. Balancing sits between matching and
sequencing so composition and ordering stay separate concerns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .albums import AlbumProfiles
from .balancing import BalanceConfig, BalanceReport, balance_candidates
from .catalog import read_catalog
from .core import (
    PlaylistMatcher,
    Track,
    artist_profiles,
    canonical_tags,
    cornerstone_artists,
    load_yaml,
    playlist_path_keys,
    safe_name,
    write_m3u,
)
from .definitions import all_definition_errors
from .identity import IdentityResolver
from .manifests import MANIFEST_DIRECTORY, build_entries, write_manifest
from .popularity import DEFAULT_PATH as POPULARITY_PATH
from .popularity import PopularityData
from .scoring import PopularityIndex, build_popularity
from .sequencing import interleaved_order, sequence_tracks


@dataclass
class PlaylistResult:
    """Everything one playlist/target combination produced."""

    name: str
    target: str
    source: str
    definition: Mapping
    candidates: list[Track] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    balance: BalanceReport | None = None
    written: bool = False
    path: Path | None = None
    capacity_limit: int | None = None
    capacity_removed: int = 0

    @property
    def count(self) -> int:
        return len(self.tracks)


@dataclass
class Project:
    """A loaded project: catalog, taxonomy, identities, and settings."""

    root: Path
    catalog: list[Track]
    settings: dict
    tag_config: dict
    identity_config: dict
    artist_config: dict
    definitions: list[dict]
    resolver: IdentityResolver
    profiles: dict[str, dict]
    cornerstones: set[str]
    sources: dict[str, list[Track]]
    album_profiles: AlbumProfiles
    album_flagged_tracks: int = 0
    excluded_artists: set[str] = field(default_factory=set)
    popularity: PopularityIndex = field(default_factory=PopularityIndex)
    measured_popularity: PopularityData | None = None

    @classmethod
    def load(cls, root: Path) -> Project:
        settings = load_yaml(root / "config/settings.yaml")
        catalog = cls._load_catalog(root, settings)
        tag_config = load_yaml(root / "taxonomy/tags.yaml")
        artist_config = load_yaml(root / "profiles/artists.yaml")
        definitions = load_yaml(root / "taxonomy/playlists.yaml").get("playlists", [])
        closure, tag_aliases = canonical_tags(tag_config)

        identities_path = root / "profiles/identities.yaml"
        identity_config = load_yaml(identities_path) if identities_path.exists() else {}
        resolver = IdentityResolver.from_config(identity_config)
        # Declared identities win; every remaining catalog spelling is registered
        # so the resolver is total and reporting can enumerate real artists.
        resolver.register_observed(sorted({t.artist for t in catalog}))

        profiles = artist_profiles(artist_config, tag_config, resolver)

        albums_path = root / "profiles/albums.yaml"
        album_profiles = AlbumProfiles.from_config(
            load_yaml(albums_path) if albums_path.exists() else {},
            resolver=resolver,
            tag_aliases=tag_aliases,
            tag_closure=closure,
        )
        errors = all_definition_errors(definitions) + album_profiles.errors(closure)
        if errors:
            raise ValueError(
                "Invalid knowledge files:\n" + "\n".join(f"  {e}" for e in errors)
            )
        # Album knowledge is applied to the catalog before anything else reads
        # it, so version classification and balancing both see the same flags.
        identity_of = {t.artist: resolver.resolve(t.artist) for t in catalog}
        catalog, album_flagged = album_profiles.apply_flags(catalog, identity_of)

        excluded = {
            resolver.resolve(str(name))
            for name in (identity_config.get("excluded_artists") or [])
        }

        # Song prominence is inferred once from the whole catalog, so scores are
        # stable no matter which playlist or target is being built.
        measured = PopularityData.load(root / POPULARITY_PATH)
        popularity = build_popularity(
            catalog,
            identity_of,
            artist_config.get("artists") or {},
            measured=measured,
            resolve_name=resolver.resolve,
        )

        sources = {
            name: cls._source_tracks(root, catalog, settings, name)
            for name in settings["sources"]
        }
        return cls(
            root=root,
            catalog=catalog,
            album_profiles=album_profiles,
            album_flagged_tracks=album_flagged,
            excluded_artists=excluded,
            popularity=popularity,
            measured_popularity=measured,
            settings=settings,
            tag_config=tag_config,
            identity_config=identity_config,
            artist_config=artist_config,
            definitions=definitions,
            resolver=resolver,
            profiles=profiles,
            cornerstones=cornerstone_artists(profiles),
            sources=sources,
        )

    @staticmethod
    def _load_catalog(root: Path, settings: dict) -> list[Track]:
        """Load the catalog from the library export, the single source of truth."""
        config = settings.get("catalog") or {}
        export = root / str(config.get("source", "Library Playlist.m3u8"))
        return read_catalog(export, str(config.get("windows_root", "")))

    @staticmethod
    def _source_tracks(root: Path, catalog, settings: dict, source_name: str) -> list[Track]:
        source = settings["sources"][source_name]
        scope = source.get("scope", "full_catalog")
        if scope == "full_catalog":
            return list(catalog)
        if scope == "playlist_intersection":
            keys = playlist_path_keys(root / source["playlist"], source["windows_root"])
            return [track for track in catalog if track.key in keys]
        raise ValueError(f"Unsupported source scope: {scope}")

    # -- helpers -----------------------------------------------------------

    @property
    def canonical_source(self) -> str:
        """The source every playlist is defined against (the full catalog).

        Other targets are intersections of the canonical playlist with their own
        library, so no playlist maintains a separate portable definition.
        """
        configured = self.settings.get("canonical_source")
        if configured:
            return str(configured)
        for name, source in self.settings["sources"].items():
            if source.get("scope", "full_catalog") == "full_catalog":
                return name
        raise ValueError("No canonical full_catalog source is configured")

    @property
    def identity_of(self) -> dict[str, str]:
        """Raw catalog artist name -> canonical identity."""
        return {t.artist: self.resolver.resolve(t.artist) for t in self.catalog}

    def balance_config(self, definition: Mapping) -> BalanceConfig:
        """Merge global defaults, per-type defaults, then per-playlist overrides."""
        defaults = dict(self.settings.get("balancing_defaults") or {})
        by_type = (self.settings.get("balancing_by_type") or {}).get(
            str(definition.get("type", "")), {}
        )
        defaults.update(by_type or {})
        return BalanceConfig.from_definition(definition, defaults)

    def matcher(self, definition: Mapping) -> PlaylistMatcher:
        """Compile a definition, applying the global artist exclusions.

        Excluded artists are non-artist folders (various-artists compilations,
        tribute series). A collection may still name one explicitly.
        """
        definition = dict(definition)
        if self.excluded_artists:
            named = self.resolver.expand(
                [str(a) for a in definition.get("include_artists", [])]
            )
            excludes = list(definition.get("exclude_artists", []))
            excludes.extend(sorted(self.excluded_artists - named))
            definition["exclude_artists"] = excludes
        return PlaylistMatcher(
            definition,
            self.profiles,
            self.resolver,
            self.album_profiles,
            tag_aliases=canonical_tags(self.tag_config)[1],
        )


def apply_capacity(
    tracks: list[Track], limit: int | None, seed: str, score_of=None
) -> tuple[list[Track], int]:
    """Trim a playlist to a device capacity limit.

    Rockbox refuses playlists beyond its configured maximum ("playlist buffer
    full"), so a portable playlist has a hard ceiling the full library does not.

    Truncating the sequenced order would take an alphabetical or album-order
    prefix and lose whole artists. Instead the candidates are rotated by artist
    and album first, so the retained slice spans the whole playlist, and the
    playlist's own sequencing strategy is applied afterwards. Deterministic:
    the same catalog always yields the same slice.
    """
    if not limit or limit <= 0 or len(tracks) <= limit:
        return tracks, 0
    rotated = interleaved_order(tracks, seed=seed, score_of=score_of)
    return rotated[:limit], len(tracks) - limit


def generate_playlists(project: Project, write: bool = True) -> list[PlaylistResult]:
    """Run the full pipeline for every playlist and target."""
    identity_of = project.identity_of
    popularity = project.popularity

    def score_of(track: Track) -> float:
        return popularity.score_of(track, identity_of)

    results: list[PlaylistResult] = []

    canonical_source = project.canonical_source
    source_keys = {
        name: {t.key for t in tracks} for name, tracks in project.sources.items()
    }

    for definition in project.definitions:
        filename = safe_name(definition["name"]) + ".m3u8"
        matcher = project.matcher(definition)
        config = project.balance_config(definition)
        targets = definition.get("targets", list(project.settings["outputs"]))

        # The canonical playlist is built once from the full catalog. Every
        # target is a subset of it, so a portable playlist can never contain a
        # track the full playlist rejected.
        canonical_candidates = matcher.select(project.sources[canonical_source])
        canonical, canonical_report = balance_candidates(
            canonical_candidates,
            config,
            identity_of=identity_of,
            cornerstones=project.cornerstones,
            score_of=score_of,
        )

        for target in targets:
            output = project.settings["outputs"][target]
            source_name = output["source"]

            if source_name == canonical_source:
                candidates = canonical_candidates
                selected, balance_report = canonical, canonical_report
            else:
                # Intersect with the target library, then re-balance. Removing
                # absent artists can leave the remainder lopsided, and
                # re-balancing only ever removes, so the subset property holds.
                candidates = [
                    t for t in canonical_candidates if t.key in source_keys[source_name]
                ]
                intersected = [t for t in canonical if t.key in source_keys[source_name]]
                selected, balance_report = balance_candidates(
                    intersected,
                    config,
                    identity_of=identity_of,
                    cornerstones=project.cornerstones,
                    score_of=score_of,
                )

            # Device capacity is a property of the target, not the playlist.
            limit = output.get("max_tracks")
            limit = int(limit) if limit else None
            selected, capacity_removed = apply_capacity(
                selected,
                limit,
                seed=f"{definition['name']}|{target}|capacity",
                score_of=score_of,
            )

            ordered = sequence_tracks(selected, definition, score_of=score_of)

            path = project.root / output["directory"] / filename
            written = False
            if write:
                count = write_m3u(path, ordered, output["root"])
                written = count > 0
                # The manifest is the contract for synchronization targets. It
                # is written for the canonical target only: Plex mirrors the
                # full library, not the portable subset.
                if source_name == canonical_source and ordered:
                    write_manifest(
                        project.root / MANIFEST_DIRECTORY,
                        definition["name"],
                        target,
                        build_entries(ordered, output["root"]),
                    )

            results.append(
                PlaylistResult(
                    name=definition["name"],
                    target=target,
                    source=source_name,
                    definition=definition,
                    candidates=candidates,
                    tracks=ordered,
                    balance=balance_report,
                    written=written,
                    path=path,
                    capacity_limit=limit,
                    capacity_removed=capacity_removed,
                )
            )
    return results


def prune_stale_outputs(project: Project, results: Sequence[PlaylistResult]) -> list[str]:
    """Remove M3U files and manifests the current taxonomy does not produce.

    A playlist that is renamed, deleted, or drops to zero tracks leaves its
    previous files on disk. A downstream reader would take a stale M3U for
    current output, and ``plex sync --apply`` would recreate a deleted playlist
    from a stale manifest.
    """
    expected: dict[tuple[Path, str], set[str]] = {}
    for output in project.settings["outputs"].values():
        expected[(project.root / output["directory"], "*.m3u8")] = set()
    manifests = (project.root / MANIFEST_DIRECTORY, "*.json")
    expected[manifests] = set()
    canonical_source = project.canonical_source
    for result in results:
        if result.path is None or result.count == 0:
            continue
        expected.setdefault((result.path.parent, "*.m3u8"), set()).add(result.path.name)
        if result.source == canonical_source:
            expected[manifests].add(f"{safe_name(result.name)}.json")

    removed: list[str] = []
    for (directory, pattern), keep in expected.items():
        if not directory.exists():
            continue
        for existing in sorted(directory.glob(pattern)):
            if existing.name not in keep:
                existing.unlink()
                removed.append(str(existing.relative_to(project.root)))
    return removed
