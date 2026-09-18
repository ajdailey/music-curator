from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .albums import AlbumProfiles
from .core import artist_profiles, canonical_tags, load_yaml
from .definitions import all_definition_errors
from .identity import IdentityResolver
from .pipeline import Project, generate_playlists, prune_stale_outputs
from .reporting import build_report, summarize, write_report
from .sequencing import KNOWN_STRATEGIES

REPORT_PATH = "reports/audit-report.json"
SUMMARY_PATH = "reports/generation-summary.json"


def generate(project_root: Path) -> int:
    project = Project.load(project_root)
    results = generate_playlists(project, write=True)
    removed = prune_stale_outputs(project, results)

    counts: dict[str, dict[str, int]] = {}
    for result in results:
        counts.setdefault(result.name, {})[result.target] = result.count

    empty = sorted({r.name for r in results if r.count == 0})
    summary = {
        "sources": {name: len(tracks) for name, tracks in project.sources.items()},
        "playlists": counts,
        "empty_playlists_not_written": empty,
        "stale_files_removed": removed,
    }
    (project_root / SUMMARY_PATH).write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    if empty:
        print(
            f"\n{len(empty)} playlist(s) produced no tracks for at least one target "
            "and were not written. Run 'music-playlists report' for explanations.",
            file=sys.stderr,
        )
    return 0


def report(project_root: Path) -> int:
    project = Project.load(project_root)
    results = generate_playlists(project, write=False)
    data = build_report(project, results)
    write_report(project, data, project_root / REPORT_PATH)
    print("\n".join(summarize(data)))
    print(f"\nFull report written to {REPORT_PATH}")
    return 0


def validate(project_root: Path) -> int:
    """Structural validation of the knowledge files (no catalog required)."""
    tag_config = load_yaml(project_root / "taxonomy/tags.yaml")
    artist_config = load_yaml(project_root / "profiles/artists.yaml")
    playlists = load_yaml(project_root / "taxonomy/playlists.yaml").get("playlists", [])
    identities_path = project_root / "profiles/identities.yaml"
    identity_config = load_yaml(identities_path) if identities_path.exists() else {}
    albums_path = project_root / "profiles/albums.yaml"
    album_config = load_yaml(albums_path) if albums_path.exists() else {}

    errors: list[str] = []
    warnings: list[str] = []

    closure, tag_aliases = canonical_tags(tag_config)
    resolver = IdentityResolver.from_config(identity_config)
    errors.extend(
        f"Identity collision on key '{collision.key}': "
        f"{collision.canonical} conflicts with {collision.other}"
        for collision in resolver.collisions
    )

    profiles = artist_profiles(artist_config, tag_config, resolver)
    known_tags: set[str] = set()
    for profile in profiles.values():
        known_tags.update(profile["tags"])
        for tag in profile["explicit_tags"]:
            canonical = tag_aliases.get(tag.casefold(), tag)
            if canonical not in closure:
                errors.append(
                    f"Artist '{profile['name']}' references undeclared tag '{tag}'"
                )

    seen_names: set[str] = set()
    referenced: set[str] = set()
    for definition in playlists:
        name = str(definition.get("name", ""))
        if not name:
            errors.append("Playlist definition is missing a name")
            continue
        if name in seen_names:
            errors.append(f"Duplicate playlist name: {name}")
        seen_names.add(name)

        for key in ("match_any_tags", "match_all_tags"):
            for tag in definition.get(key, []):
                canonical = tag_aliases.get(str(tag).casefold(), str(tag))
                referenced.add(canonical)
                if canonical not in closure:
                    errors.append(
                        f"Playlist '{name}' references undeclared tag '{tag}'"
                    )

        strategy = (definition.get("ordering") or {}).get("strategy", "album_order")
        if strategy not in KNOWN_STRATEGIES:
            errors.append(f"Playlist '{name}' uses unknown ordering strategy '{strategy}'")

        balancing = definition.get("balancing") or {}
        share = balancing.get("max_share_per_artist")
        if share is not None and not (0 < float(share) <= 1):
            errors.append(
                f"Playlist '{name}' has max_share_per_artist={share}; expected a fraction in (0, 1]"
            )
        if definition.get("type") == "collection" and balancing.get("enabled"):
            warnings.append(
                f"Playlist '{name}' is a collection with balancing enabled; "
                "collections should preserve complete discographies"
            )

    errors.extend(all_definition_errors(playlists))
    album_profiles = AlbumProfiles.from_config(
        album_config, resolver=resolver, tag_aliases=tag_aliases, tag_closure=closure
    )
    errors.extend(album_profiles.errors(closure))

    unpopulated = sorted(referenced - known_tags)
    warnings.extend(
        f"Tag '{tag}' is referenced by a playlist but no artist carries it"
        for tag in unpopulated
    )

    result = {
        "artist_profiles": len(profiles),
        "playlist_definitions": len(playlists),
        "declared_identities": len(resolver.identities),
        "declared_tags": len(closure),
        "profile_tags": sorted(known_tags),
        "unpopulated_playlist_tags": unpopulated,
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2))
    if errors:
        print(f"\nvalidate FAILED with {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"\nvalidate OK ({len(warnings)} warning(s))", file=sys.stderr)
    return 0


def _project_parser() -> argparse.ArgumentParser:
    """Shared --project flag, accepted before or after the subcommand."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", type=Path, default=None)
    return common


def _add_plex_commands(subparsers, common) -> None:
    plex = subparsers.add_parser(
        "plex", help="Synchronize playlists into Plex", parents=[common]
    )
    plex_commands = plex.add_subparsers(dest="plex_command", required=True)

    plex_commands.add_parser(
        "status", help="Verify connection, auth, and library", parents=[common]
    )

    index_parser = plex_commands.add_parser(
        "index", help="Cache the Plex library", parents=[common]
    )
    index_parser.add_argument(
        "--refresh", action="store_true", help="Rebuild the cache from scratch"
    )
    index_parser.add_argument(
        "--resume", action="store_true", help="Continue an interrupted index"
    )
    index_parser.add_argument(
        "--batch-size", type=int, default=500, help="Tracks per request (default 500)"
    )
    index_parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=5000,
        help="Save progress every N tracks (default 5000)",
    )

    popularity_parser = plex_commands.add_parser(
        "popularity",
        parents=[common],
        help="Export song popularity from the index into a knowledge file",
    )
    popularity_parser.add_argument("--output", help="Override the knowledge file path")

    resolve_parser = plex_commands.add_parser(
        "resolve", parents=[common], help="Match manifests to Plex tracks (no writes)"
    )
    resolve_parser.add_argument("--playlist", help="Limit to one playlist")
    resolve_parser.add_argument(
        "--verbose", action="store_true", help="List unresolved and ambiguous tracks"
    )

    diff_parser = plex_commands.add_parser(
        "diff", parents=[common], help="Compare generated playlists with Plex (no writes)"
    )
    diff_parser.add_argument("--playlist", help="Limit to one playlist")

    sync_parser = plex_commands.add_parser(
        "sync", parents=[common], help="Synchronize playlists into Plex (dry run by default)"
    )
    sync_parser.add_argument("--playlist", help="Limit to one playlist")
    sync_parser.add_argument("--all", action="store_true", help="Every playlist (default)")
    sync_parser.add_argument(
        "--create-only", action="store_true", help="Never modify an existing playlist"
    )
    sync_parser.add_argument(
        "--replace", action="store_true", help="Replace existing playlists (default)"
    )
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="Explicitly request a dry run (default)"
    )
    sync_parser.add_argument(
        "--apply", action="store_true", help="Actually write to Plex"
    )
    sync_parser.add_argument(
        "--max-unresolved", type=float, default=5.0, help="Refuse above this %% unresolved"
    )
    sync_parser.add_argument(
        "--max-ambiguous", type=float, default=2.0, help="Refuse above this %% ambiguous"
    )


def main() -> None:
    common = _project_parser()
    parser = argparse.ArgumentParser(prog="music-playlists", parents=[common])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("generate", "Write playlists for every target"),
        ("validate", "Check the knowledge files"),
        ("report", "Build the audit report"),
    ):
        subparsers.add_parser(name, help=help_text, parents=[common])
    _add_plex_commands(subparsers, common)

    # Opt-in only: Last.fm is never part of generate, report, or plex sync.
    lastfm = subparsers.add_parser(
        "lastfm", help="Optional Last.fm enrichment (off by default)", parents=[common]
    )
    lastfm_commands = lastfm.add_subparsers(dest="lastfm_command", required=True)
    enrich_parser = lastfm_commands.add_parser(
        "enrich",
        parents=[common],
        help="Fill songs with no listener count from Last.fm",
    )
    enrich_parser.add_argument(
        "--limit", type=int, default=0, help="Stop after N lookups (0 = all)"
    )
    enrich_parser.add_argument(
        "--dry-run", action="store_true", help="Report the gap without querying"
    )

    args = parser.parse_args()
    if args.project is None:
        args.project = Path.cwd()
    if args.command == "plex":
        from . import plex_cli

        raise SystemExit(plex_cli.run(args.project, args))
    if args.command == "lastfm":
        from . import lastfm_cli

        raise SystemExit(lastfm_cli.run(args.project, args))

    commands = {"generate": generate, "validate": validate, "report": report}
    raise SystemExit(commands[args.command](args.project))


if __name__ == "__main__":
    main()
