"""The `music-playlists lastfm ...` commands.

Opt-in only. Nothing here is part of generate, report, or the Plex sync flow.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .integrations.lastfm.client import (
    DEFAULT_CACHE_PATH,
    LastfmAuthError,
    LastfmClient,
    LastfmRequestError,
    LookupCache,
)
from .integrations.lastfm.config import (
    ENV_API_KEY,
    LastfmConfigurationError,
    LastfmSettings,
)
from .pipeline import Project
from .popularity import DEFAULT_PATH as POPULARITY_PATH
from .popularity import PopularityData, song_key
from .versions import base_title

#: Consecutive failed requests after which enrichment stops, so an unreachable
#: Last.fm does not cost one timeout per remaining song.
MAX_CONSECUTIVE_FAILURES = 10


def enrich(project_root: Path, args) -> int:
    """Fill songs that have no listener count, using Last.fm."""
    settings = LastfmSettings.from_env(project_root)
    project = Project.load(project_root)
    identity_of = project.identity_of

    path = project_root / POPULARITY_PATH
    data = PopularityData.load(path) or PopularityData(source="lastfm")

    # Every distinct song in the catalog, with a display spelling.
    songs: dict[str, tuple[str, str]] = {}
    for track in project.catalog:
        identity = identity_of.get(track.artist, track.artist)
        key = song_key(identity, track.title)
        songs.setdefault(key, (identity, base_title(track.title) or track.title))

    gaps = [k for k in songs if not data.songs.get(k, {}).get("listeners")]
    gaps.sort()
    if args.limit:
        gaps = gaps[: args.limit]

    cache = LookupCache.load(project_root / DEFAULT_CACHE_PATH)
    print(
        f"{len(songs)} distinct songs, {len(gaps)} without a listener count.\n"
        f"Last.fm allows {settings.rate_limit:.0f} requests/sec, so this takes "
        f"about {len(gaps) / max(settings.rate_limit, 0.1) / 60:.0f} minutes. "
        "Progress is cached; rerunning resumes."
    )
    if args.dry_run:
        print("\nDry run. No requests made.")
        return 0

    client = LastfmClient(settings)
    filled = missing = failed = consecutive = 0
    stopped = False
    started = time.time()

    def save() -> None:
        cache.save(project_root / DEFAULT_CACHE_PATH)
        data.save(path)

    try:
        for position, key in enumerate(gaps, start=1):
            identity, title = songs[key]
            cached = cache.get(key)
            if cached is None:
                try:
                    info = client.track_info(identity, title)
                except LastfmRequestError as exc:
                    failed += 1
                    consecutive += 1
                    print(f"  lookup failed ({exc}): {identity} - {title}", file=sys.stderr)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        stopped = True
                        break
                    continue
                consecutive = 0
                if info is None:
                    cache.put(key, 0, 0, found=False)
                else:
                    cache.put(key, info["listeners"], info["playcount"], found=True)
                cached = cache.get(key)

            if cached.get("found") and cached.get("listeners"):
                row = dict(data.songs.get(key, {}))
                row["listeners"] = int(cached["listeners"])
                row.setdefault("plays", 0)
                row["source"] = "lastfm"
                row.setdefault("title", title)
                data.songs[key] = row
                filled += 1
            else:
                missing += 1

            if position % 500 == 0:
                save()
                rate = position / max(time.time() - started, 0.01)
                print(
                    f"  {position}/{len(gaps)} looked up, {filled} filled "
                    f"({rate:.1f}/s, "
                    f"~{(len(gaps) - position) / max(rate, 0.01) / 60:.0f} min left)",
                    flush=True,
                )
    except KeyboardInterrupt:
        save()
        print(
            f"\nInterrupted after {filled} filled. Progress saved; rerun to continue.",
            file=sys.stderr,
        )
        return 130
    except LastfmAuthError as exc:
        save()
        print(
            f"\n{exc} Check {ENV_API_KEY}. "
            f"Progress saved after {filled} filled.",
            file=sys.stderr,
        )
        return 2

    save()
    print(
        json.dumps(
            {
                "looked_up": len(gaps),
                "filled": filled,
                "not_found": missing,
                "failed": failed,
                "requests_made": client.requests_made,
                "elapsed_minutes": round((time.time() - started) / 60, 1),
                "popularity_file": str(path.relative_to(project_root)),
            },
            indent=2,
        )
    )
    if stopped:
        print(
            f"\nStopped after {MAX_CONSECUTIVE_FAILURES} consecutive failed "
            "requests. Progress saved; rerun to continue.",
            file=sys.stderr,
        )
        return 1
    if failed:
        print(
            f"\n{failed} lookup(s) failed and were not cached; rerun to retry them.",
            file=sys.stderr,
        )
        return 1
    print("\nRe-run 'generate' to apply.", file=sys.stderr)
    return 0


COMMANDS = {"enrich": enrich}


def run(project_root: Path, args) -> int:
    try:
        return COMMANDS[args.lastfm_command](project_root, args)
    except LastfmConfigurationError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
