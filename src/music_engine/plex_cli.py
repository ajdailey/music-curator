"""The `music-playlists plex ...` commands.

Every command fails clean when configuration is missing, and every write is
gated behind an explicit --apply.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .integrations.plex.cache import DEFAULT_CACHE_PATH, PlexIndex, cache_path
from .integrations.plex.client import (
    PlexClient,
    PlexUnavailableError,
    request_errors,
    safe_reason,
)
from .integrations.plex.config import PlexConfigurationError, PlexSettings
from .integrations.plex.resolver import PlexResolver
from .integrations.plex.synchronizer import PlexSynchronizer, SyncThresholds, managed_title
from .manifests import MANIFEST_DIRECTORY, load_manifests
from .pipeline import Project
from .popularity import DEFAULT_PATH as POPULARITY_PATH
from .popularity import build_from_plex_tracks


def _settings(project_root: Path) -> PlexSettings:
    return PlexSettings.from_env(project_root)


def _manifests(project_root: Path, playlist: str | None) -> dict:
    directory = project_root / MANIFEST_DIRECTORY
    manifests = load_manifests(directory)
    if not manifests:
        raise SystemExit(
            f"No playlist manifests found in {directory}.\n"
            "Run 'music-playlists generate' first."
        )
    if playlist:
        if playlist not in manifests:
            available = ", ".join(sorted(manifests)[:10])
            raise SystemExit(
                f"Unknown playlist {playlist!r}. Available (first 10): {available}"
            )
        return {playlist: manifests[playlist]}
    return manifests


def _load_index(
    project_root: Path, settings: PlexSettings, server_identifier: str | None = None
) -> PlexIndex:
    """Load the cached index, refusing one built from another library or server.

    The server check runs only when ``server_identifier`` is given; `resolve`
    and `popularity` read the cache without connecting to Plex.
    """
    index = PlexIndex.load(cache_path(project_root))
    if index is None:
        raise SystemExit(
            f"No Plex index cached at {DEFAULT_CACHE_PATH}.\n"
            "Run 'music-playlists plex index' first."
        )
    if index.library != settings.library:
        raise SystemExit(
            f"Cached index is for library {index.library!r} but configuration "
            f"selects {settings.library!r}. Run 'music-playlists plex index --refresh'."
        )
    if server_identifier is not None and not index.matches(
        server_identifier, settings.library
    ):
        raise SystemExit(
            f"Cached index is for server {index.server_identifier!r} but "
            f"{settings.url} is server {server_identifier!r}. "
            "Run 'music-playlists plex index --refresh'."
        )
    if not index.complete:
        raise SystemExit(
            f"The Plex index is incomplete ({len(index.tracks)} of "
            f"{index.expected_total or '?'} tracks). Resolving against it would "
            "report tracks as missing that simply have not been indexed yet.\n"
            "Finish it with: music-playlists plex index --resume"
        )
    return index


def _resolver(
    project_root: Path, settings: PlexSettings, client: PlexClient | None = None
) -> PlexResolver:
    identifier = client.server.machineIdentifier if client is not None else None
    index = _load_index(project_root, settings, identifier)
    return PlexResolver(index.tracks, media_root=settings.media_root)


# -- commands --------------------------------------------------------------


def status(project_root: Path, args) -> int:
    settings = _settings(project_root)
    client = PlexClient(settings)
    result = client.status()
    print(json.dumps(result, indent=2))

    index = PlexIndex.load(cache_path(project_root))
    if index is None:
        print("\nNo local index cached. Run 'music-playlists plex index'.", file=sys.stderr)
    else:
        print(
            f"\nLocal index: {len(index.tracks)} tracks, "
            f"{index.age_hours:.1f} hours old",
            file=sys.stderr,
        )
    if not result["selected_library_available"]:
        print(
            f"\nLibrary {settings.library!r} not found on this server.",
            file=sys.stderr,
        )
        return 1
    return 0


def index(project_root: Path, args) -> int:
    settings = _settings(project_root)
    path = cache_path(project_root)
    existing = PlexIndex.load(path)

    if existing is not None and existing.complete and not args.refresh:
        print(
            json.dumps(
                {
                    "cached": True,
                    "complete": True,
                    "tracks": len(existing.tracks),
                    "library": existing.library,
                    "age_hours": round(existing.age_hours, 2),
                    "path": str(path.relative_to(project_root)),
                },
                indent=2,
            )
        )
        print("\nPass --refresh to rebuild.", file=sys.stderr)
        return 0

    if existing is not None and not existing.complete and not (args.resume or args.refresh):
        print(
            f"A partial index exists ({len(existing.tracks)} of "
            f"{existing.expected_total or '?'} tracks).\n"
            "Continue with --resume, or start over with --refresh.",
            file=sys.stderr,
        )
        return 1

    client = PlexClient(settings)
    server = client.server

    # Resume a partial index rather than repeating hours of work.
    tracks: list = []
    start = 0
    if args.resume and existing is not None and not existing.complete:
        if not existing.matches(server.machineIdentifier, settings.library):
            raise SystemExit(
                f"Partial index is for library {existing.library!r} on server "
                f"{existing.server_identifier!r}, not {settings.library!r} on "
                f"{server.machineIdentifier!r}. Re-run with --refresh."
            )
        tracks = list(existing.tracks)
        start = existing.next_start
        print(f"Resuming from track {start} ({len(tracks)} already indexed).")

    total = client.track_total()
    started = time.time()
    print(
        f"Indexing {settings.library!r}: {total or 'unknown'} tracks, "
        f"batch size {args.batch_size}. This can take a while on a large "
        "library; progress is checkpointed and can be resumed."
    )

    def snapshot(complete: bool, next_start: int) -> PlexIndex:
        return PlexIndex(
            tracks=tracks,
            created_at=time.time(),
            server_identifier=server.machineIdentifier,
            library=settings.library,
            complete=complete,
            next_start=next_start,
            expected_total=total,
        )

    last_checkpoint = 0
    # The offset of the first track not yet in `tracks`, so a resume neither
    # repeats nor skips one.
    next_start = start
    try:
        for position, track in client.iter_tracks(
            start=start, batch_size=args.batch_size
        ):
            tracks.append(track)
            next_start = position + 1
            done = len(tracks)
            if done - last_checkpoint >= args.checkpoint_every:
                snapshot(False, next_start).save(path)
                last_checkpoint = done
                rate = done / max(time.time() - started, 0.01)
                remaining = (total - done) / rate if total and rate else 0
                print(
                    f"  {done}/{total or '?'} tracks "
                    f"({rate:.0f}/s, ~{remaining / 60:.0f} min remaining)",
                    flush=True,
                )
    except KeyboardInterrupt:
        snapshot(False, next_start).save(path)
        print(
            f"\nInterrupted at {len(tracks)} tracks. Progress saved.\n"
            "Resume with: music-playlists plex index --resume",
            file=sys.stderr,
        )
        return 130

    if total and len(tracks) != total:
        snapshot(False, next_start).save(path)
        print(
            f"\nIndexed {len(tracks)} tracks but the library reports {total}. "
            "The index is saved as incomplete.\n"
            "Rebuild it with: music-playlists plex index --refresh",
            file=sys.stderr,
        )
        return 1

    snapshot(True, next_start).save(path)
    with_paths = sum(1 for t in tracks if t.media_path)
    print(
        json.dumps(
            {
                "cached": False,
                "complete": True,
                "tracks": len(tracks),
                "expected_total": total,
                "tracks_with_media_path": with_paths,
                "elapsed_minutes": round((time.time() - started) / 60, 1),
                "library": settings.library,
                "path": str(path.relative_to(project_root)),
                "sample_media_paths": [t.media_path for t in tracks[:3] if t.media_path],
            },
            indent=2,
        )
    )
    return 0


def popularity(project_root: Path, args) -> int:
    """Export song popularity from the cached index into a knowledge file.

    Generation reads the knowledge file, never Plex, so playlists stay
    deterministic and buildable offline.
    """
    settings = _settings(project_root)
    index = _load_index(project_root, settings)
    project = Project.load(project_root)

    data = build_from_plex_tracks(
        index.tracks,
        identity_of=project.identity_of,
        source="plex",
        library=settings.library,
        generated_at=time.time(),
    )
    path = project_root / (getattr(args, "output", None) or POPULARITY_PATH)
    data.save(path)

    print(
        json.dumps(
            {
                "path": str(path.relative_to(project_root)),
                "songs": len(data.songs),
                "songs_with_listeners": data.covered_songs,
                "coverage_pct": round(
                    100 * data.covered_songs / max(1, len(data.songs)), 2
                ),
                "source_tracks": len(index.tracks),
                "library": settings.library,
            },
            indent=2,
        )
    )
    print(
        "\nGeneration uses this automatically. Re-run 'generate' to apply it.",
        file=sys.stderr,
    )
    return 0


def resolve(project_root: Path, args) -> int:
    settings = _settings(project_root)
    resolver = _resolver(project_root, settings)
    manifests = _manifests(project_root, args.playlist)

    summaries = []
    for playlist, entries in manifests.items():
        summary = resolver.resolve(playlist, entries)
        summaries.append(summary)
        print(
            f"{playlist:34} desired={summary.desired:6} matched={summary.matched:6} "
            f"ambiguous={summary.ambiguous:4} unresolved={summary.unresolved:4} "
            f"({100 * summary.match_rate:.1f}%)"
        )
        if args.verbose:
            for problem in summary.problems()[:10]:
                print(f"    {problem.status}: {problem.entry.relative_path}")

    report = {
        "playlists": [s.as_dict(include_details=True) for s in summaries],
        "totals": {
            "desired": sum(s.desired for s in summaries),
            "matched": sum(s.matched for s in summaries),
            "ambiguous": sum(s.ambiguous for s in summaries),
            "unresolved": sum(s.unresolved for s in summaries),
        },
    }
    output = project_root / "reports/plex-resolution.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nDetail written to reports/plex-resolution.json")
    return 0


def diff(project_root: Path, args) -> int:
    settings = _settings(project_root)
    client = PlexClient(settings)
    synchronizer = PlexSynchronizer(client, _resolver(project_root, settings, client))
    manifests = _manifests(project_root, args.playlist)

    rows = []
    for playlist, entries in manifests.items():
        difference, _ = synchronizer.diff(playlist, entries)
        rows.append(difference.as_dict())
        state = "in sync" if difference.in_sync else "DIFFERS"
        exists = "exists" if difference.exists else "missing"
        print(
            f"{playlist:34} {exists:8} {state:8} "
            f"desired={difference.desired:6} actual={difference.actual:6} "
            f"+{difference.to_add} -{difference.to_remove} "
            f"reorder={'yes' if difference.reorder_required else 'no'}"
        )

    output = project_root / "reports/plex-diff.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"playlists": rows}, indent=2), encoding="utf-8")
    print("\nDetail written to reports/plex-diff.json")
    return 0


def sync(project_root: Path, args) -> int:
    settings = _settings(project_root)
    client = PlexClient(settings)
    synchronizer = PlexSynchronizer(
        client,
        _resolver(project_root, settings, client),
        SyncThresholds(
            max_unresolved_pct=args.max_unresolved,
            max_ambiguous_pct=args.max_ambiguous,
        ),
    )
    manifests = _manifests(project_root, args.playlist)

    if args.apply:
        print(f"APPLYING changes to {settings.url} (library {settings.library!r})\n")
    else:
        print("DRY RUN. No changes will be made. Pass --apply to write.\n")

    errors = request_errors()
    outcomes = []
    for playlist, entries in manifests.items():
        try:
            outcome = synchronizer.sync(
                playlist, entries, apply=args.apply, create_only=args.create_only
            ).as_dict()
        except errors as exc:
            outcome = {
                "playlist": playlist,
                "managed_title": managed_title(playlist),
                "action": "error",
                "applied": False,
                "reason": f"failed: {safe_reason(exc)}",
                "desired": 0,
                "previous": 0,
                "verified": False,
            }
        outcomes.append(outcome)
        print(
            f"{playlist:34} {outcome['action']:11} "
            f"desired={outcome['desired']:6} previous={outcome['previous']:6} "
            f"{outcome['reason']}"
        )

    output = project_root / "reports/plex-sync.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"applied": args.apply, "playlists": outcomes}, indent=2),
        encoding="utf-8",
    )
    print("\nDetail written to reports/plex-sync.json")

    refused = [o for o in outcomes if o["action"] == "skip" and "refused" in o["reason"]]
    failed = [o for o in outcomes if o["action"] == "error"]
    unverified = [o for o in outcomes if o["applied"] and not o["verified"]]
    if refused:
        print(f"{len(refused)} playlist(s) refused on quality thresholds.", file=sys.stderr)
    if failed:
        print(f"{len(failed)} playlist(s) failed on a Plex request error.", file=sys.stderr)
    if unverified:
        print(
            f"{len(unverified)} playlist(s) written but failed verification.",
            file=sys.stderr,
        )
    return 1 if refused or failed or unverified else 0


COMMANDS = {
    "status": status,
    "index": index,
    "popularity": popularity,
    "resolve": resolve,
    "diff": diff,
    "sync": sync,
}


def run(project_root: Path, args) -> int:
    try:
        return COMMANDS[args.plex_command](project_root, args)
    except PlexConfigurationError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
    except PlexUnavailableError as exc:
        print(f"\nPlex unavailable: {exc}\n", file=sys.stderr)
        return 3
    except request_errors() as exc:
        print(f"\nPlex request failed: {safe_reason(exc)}\n", file=sys.stderr)
        return 3
