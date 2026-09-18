"""The audit report: everything needed to trust (or distrust) a generation run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .analysis import coverage, domination, explain_zero, overlap_matrix
from .core import canonical_tags
from .identity import punctuation_key
from .pipeline import PlaylistResult, Project
from .versions import version_statistics


def _results_by_target(results: Sequence[PlaylistResult], target: str) -> dict[str, PlaylistResult]:
    return {r.name: r for r in results if r.target == target}


def zero_count_section(project: Project, results: Sequence[PlaylistResult]) -> list[dict]:
    """Explain every empty playlist, separating genuine absence from defects."""
    plex = _results_by_target(results, "plex")
    rows: list[dict] = []
    for result in results:
        if result.count:
            continue
        plex_result = plex.get(result.name)
        plex_tracks = plex_result.candidates if plex_result else []
        rows.append(
            explain_zero(
                playlist=result.name,
                target=result.target,
                plex_tracks=plex_tracks,
                source_tracks=project.sources[result.source],
                matched_tracks=result.tracks,
                identity_of=project.identity_of,
            )
        )
    return rows


def alias_section(project: Project) -> dict:
    """Declared identities, what they resolved to, and unresolved declarations."""
    catalog_artists = {t.artist for t in project.catalog}
    resolved_names: dict[str, list[str]] = {}
    for artist in sorted(catalog_artists):
        resolved_names.setdefault(project.resolver.resolve(artist), []).append(artist)

    declared = []
    for canonical, identity in sorted(project.resolver.identities.items()):
        if not identity.aliases and not identity.related and not identity.note:
            continue  # auto-registered catalog spelling, nothing curated to report
        spellings = resolved_names.get(canonical, [])
        declared.append(
            {
                "identity": canonical,
                "aliases": list(identity.aliases),
                "related": list(identity.related),
                "catalog_spellings": spellings,
                "catalog_tracks": sum(
                    1 for t in project.catalog if t.artist in set(spellings)
                ),
                "present_in_catalog": bool(spellings),
                "note": identity.note,
            }
        )

    # Artists whose punctuation key collides but which resolve differently:
    # a hint that an alias may be missing.
    by_punct: dict[str, set[str]] = {}
    for artist in catalog_artists:
        by_punct.setdefault(punctuation_key(artist), set()).add(
            project.resolver.resolve(artist)
        )
    suspicious = [
        {"punctuation_key": key, "identities": sorted(names)}
        for key, names in sorted(by_punct.items())
        if len(names) > 1
    ]

    return {
        "declared_identities": declared,
        "identity_collisions": [
            {"key": c.key, "identity": c.canonical, "conflicts_with": c.other}
            for c in project.resolver.collisions
        ],
        "possible_missing_aliases": suspicious,
    }


def overlap_section(project: Project, results: Sequence[PlaylistResult]) -> dict:
    pairs = [
        (str(p[0]), str(p[1]))
        for p in (project.settings.get("reporting", {}) or {}).get("overlap_pairs", [])
    ]
    output: dict[str, list[dict]] = {}
    for target in project.settings["outputs"]:
        playlists = {name: r.tracks for name, r in _results_by_target(results, target).items()}
        output[target] = overlap_matrix(playlists, pairs, project.identity_of)
    duplicates = [
        row
        for target_rows in output.values()
        for row in target_rows
        if row["track_jaccard"] >= 0.9
    ]
    return {"pairs": output, "effective_duplicates": duplicates}


def domination_section(project: Project, results: Sequence[PlaylistResult]) -> dict:
    """Artist concentration per playlist.

    Collections are single-artist by definition, so they are measured but never
    flagged: alarming on 'Bruce Springsteen Collection is 100% Bruce
    Springsteen' would bury the real signals.
    """
    identity_of = project.identity_of
    rows = []
    for result in results:
        if not result.count:
            continue
        row = domination(f"{result.name} [{result.target}]", result.tracks, identity_of).as_dict()
        row["type"] = str(result.definition.get("type", ""))
        row["balanced"] = bool(result.balance and result.balance.capped_artists)
        rows.append(row)
    rows.sort(key=lambda row: -row["top_artist_share_pct"])
    return {
        "playlists": rows,
        "dominated": [
            row
            for row in rows
            if row["type"] != "collection" and row["top_artist_share_pct"] >= 25.0
        ],
    }


def balancing_section(results: Sequence[PlaylistResult]) -> list[dict]:
    rows = []
    for result in results:
        if result.balance is None:
            continue
        data = result.balance.as_dict()
        if data["input_tracks"] == data["output_tracks"] and not data["capped_artists"]:
            continue  # balancing was disabled or a no-op
        rows.append({"playlist": result.name, "target": result.target, **data})
    return rows


def coverage_section(project: Project, results: Sequence[PlaylistResult]) -> dict:
    plex = _results_by_target(results, "plex")
    rockbox = _results_by_target(results, "rockbox")
    identity_of = project.identity_of
    rows = []
    for name, plex_result in plex.items():
        rockbox_result = rockbox.get(name)
        if rockbox_result is None:
            continue
        rows.append(
            coverage(name, plex_result.tracks, rockbox_result.tracks, identity_of)
        )
    rows.sort(key=lambda row: row["coverage_pct"])
    source_total = {
        name: len(tracks) for name, tracks in project.sources.items()
    }
    return {
        "sources": source_total,
        "portable_ratio_pct": round(
            100 * source_total.get("rockbox_ipod", 0) / max(1, source_total.get("plex_full", 1)), 2
        ),
        "playlists": rows,
        "unexpectedly_low": [
            row for row in rows if row["plex_tracks"] >= 500 and row["coverage_pct"] < 5.0
        ],
    }


def taxonomy_section(project: Project, results: Sequence[PlaylistResult]) -> dict:
    closure, aliases = canonical_tags(project.tag_config)
    declared_tags = set(closure)

    # Explicit tags are what a curator wrote; effective tags include everything
    # inherited through the hierarchy. A playlist matching 'rock' is populated
    # by artists tagged 'classic-rock', so the unpopulated check must use the
    # effective set or it reports false alarms.
    profile_tags: Counter = Counter()
    effective_tags: set[str] = set()
    for profile in project.profiles.values():
        effective_tags.update(profile["tags"])
        for tag in profile["explicit_tags"]:
            profile_tags[tag] += 1

    referenced: set[str] = set()
    for definition in project.definitions:
        for key in ("match_any_tags", "match_all_tags"):
            referenced.update(
                aliases.get(str(t).casefold(), str(t)) for t in definition.get(key, [])
            )

    catalog_artists = {t.artist for t in project.catalog}
    catalog_identities = {project.resolver.resolve(a) for a in catalog_artists}
    classified = set(project.profiles)
    track_counts = Counter(project.resolver.resolve(t.artist) for t in project.catalog)
    unclassified = sorted(
        (catalog_identities - classified), key=lambda a: (-track_counts[a], a)
    )

    generated = {r.name for r in results if r.count}
    declared_playlists = {str(d["name"]) for d in project.definitions}

    return {
        "declared_tags": len(declared_tags),
        "tag_aliases": aliases,
        "tags_used_by_playlists": sorted(referenced),
        "tags_referenced_but_unpopulated": sorted(referenced - effective_tags),
        "tags_never_assigned_directly": sorted(
            (referenced & effective_tags) - set(profile_tags)
        ),
        "tags_declared_but_unused": sorted(
            declared_tags - referenced - {"music"}
        ),
        "profiled_artists": len(project.profiles),
        "profiled_artists_absent_from_catalog": sorted(
            identity for identity in project.profiles if identity not in catalog_identities
        ),
        "catalog_artists": len(catalog_identities),
        "unclassified_artists": len(unclassified),
        "top_unclassified_artists": [
            {"artist": a, "tracks": track_counts[a]} for a in unclassified[:25]
        ],
        "declared_playlists": len(declared_playlists),
        "playlists_with_no_output": sorted(declared_playlists - generated),
    }


def capacity_section(project: Project, results: Sequence[PlaylistResult]) -> dict:
    """Playlists trimmed to fit a device limit, and what was dropped."""
    limits = {
        target: output.get("max_tracks")
        for target, output in project.settings["outputs"].items()
    }
    trimmed = [
        {
            "playlist": r.name,
            "target": r.target,
            "limit": r.capacity_limit,
            "available": r.count + r.capacity_removed,
            "kept": r.count,
            "dropped": r.capacity_removed,
            "kept_pct": round(100 * r.count / max(1, r.count + r.capacity_removed), 2),
        }
        for r in results
        if r.capacity_removed > 0
    ]
    trimmed.sort(key=lambda row: -row["dropped"])
    return {
        "limits": limits,
        "trimmed_playlists": trimmed,
        "total_tracks_dropped": sum(row["dropped"] for row in trimmed),
    }


def albums_section(project: Project) -> dict:
    """What album-level knowledge contributed."""
    stats = project.album_profiles.statistics(project.catalog, project.identity_of)
    return {
        **stats,
        "tracks_reflagged_from_album_rules": project.album_flagged_tracks,
        "excluded_artists": sorted(project.excluded_artists),
    }


def popularity_section(project: Project) -> dict:
    """How many scored songs carry a measured listener count."""
    index = project.popularity
    return {
        "measured_file_loaded": project.measured_popularity is not None,
        "scored_songs": len(index.scores),
        "measured_songs": len(index.measured),
        "measured_coverage_pct": round(100 * index.measured_coverage, 2),
    }


def versions_section(project: Project, results: Sequence[PlaylistResult]) -> dict:
    catalog_stats = version_statistics(project.catalog)
    per_playlist = []
    for result in results:
        if not result.count:
            continue
        stats = version_statistics(result.tracks)
        total = result.count
        per_playlist.append(
            {
                "playlist": result.name,
                "target": result.target,
                "tracks": total,
                "live_pct": round(
                    100 * stats["performance"].get("live", 0) / total, 2
                ),
                "remix_pct": round(
                    100 * stats["performance"].get("remix", 0) / total, 2
                ),
                "performance": stats["performance"],
                "release": stats["release"],
            }
        )
    per_playlist.sort(key=lambda row: -row["live_pct"])
    return {
        "catalog": catalog_stats,
        "playlists": per_playlist,
        "live_heavy_playlists": [
            row for row in per_playlist if row["live_pct"] >= 40.0
        ],
        # A balanced playlist should sit near zero here. Anything above 10% means
        # the quota is off, or the songs genuinely exist only as remixes.
        "remix_heavy_playlists": [
            row for row in per_playlist if row["remix_pct"] >= 10.0
        ],
    }


def build_report(project: Project, results: Sequence[PlaylistResult]) -> dict:
    """Assemble the full audit report."""
    return {
        "sources": {name: len(tracks) for name, tracks in project.sources.items()},
        "playlists": _playlist_counts(project, results),
        "zero_counts": zero_count_section(project, results),
        "identities": alias_section(project),
        "overlap": overlap_section(project, results),
        "artist_domination": domination_section(project, results),
        "balancing": balancing_section(results),
        "rockbox_coverage": coverage_section(project, results),
        "taxonomy": taxonomy_section(project, results),
        "versions": versions_section(project, results),
        "albums": albums_section(project),
        "popularity": popularity_section(project),
        "capacity": capacity_section(project, results),
    }


def _playlist_counts(project: Project, results: Sequence[PlaylistResult]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for result in results:
        counts.setdefault(result.name, {})[result.target] = result.count
    return counts


def summarize(report: dict) -> list[str]:
    """Short human-readable findings printed after a report run."""
    lines: list[str] = []
    zero = report["zero_counts"]
    defects = [z for z in zero if z["reason"] == "removed_downstream"]
    absent = [z for z in zero if z["reason"] == "absent_from_source"]
    empty = [z for z in zero if z["reason"] == "no_catalog_matches"]
    lines.append(
        f"zero-count playlists: {len(zero)} "
        f"({len(absent)} genuinely absent, {len(empty)} unpopulated taxonomy, "
        f"{len(defects)} suspected defects)"
    )
    lines.extend(
        f"  DEFECT {item['playlist']} [{item['target']}]: {item['explanation']}"
        for item in defects
    )

    dupes = report["overlap"]["effective_duplicates"]
    lines.append(f"effective duplicate playlist pairs: {len(dupes)}")
    lines.extend(
        f"  DUPLICATE {row['left']} vs {row['right']} "
        f"(jaccard {row['track_jaccard']})"
        for row in dupes
    )

    dominated = report["artist_domination"]["dominated"]
    lines.append(f"artist-dominated playlists (>=25% one artist): {len(dominated)}")
    lines.extend(
        f"  DOMINATED {row['playlist']}: {row['top_artist']} "
        f"{row['top_artist_share_pct']}%"
        for row in dominated[:10]
    )

    remix_heavy = report["versions"]["remix_heavy_playlists"]
    lines.append(f"remix-heavy playlists (>=10% remixes): {len(remix_heavy)}")
    lines.extend(
        f"  REMIXES {row['playlist']} [{row['target']}]: {row['remix_pct']}%"
        for row in remix_heavy[:10]
    )

    low = report["rockbox_coverage"]["unexpectedly_low"]
    lines.append(f"playlists with unexpectedly low portable coverage: {len(low)}")

    taxonomy = report["taxonomy"]
    lines.append(
        f"taxonomy: {taxonomy['unclassified_artists']} unclassified artists, "
        f"{len(taxonomy['tags_referenced_but_unpopulated'])} unpopulated tags, "
        f"{len(taxonomy['tags_declared_but_unused'])} unused tags, "
        f"{len(taxonomy['playlists_with_no_output'])} playlists with no output"
    )
    capacity = report["capacity"]
    if capacity["trimmed_playlists"]:
        lines.append(
            f"device capacity: {len(capacity['trimmed_playlists'])} playlist(s) "
            f"trimmed, {capacity['total_tracks_dropped']} tracks dropped "
            f"(limits: {capacity['limits']})"
        )
    albums = report["albums"]
    lines.append(
        f"album knowledge: {albums['declared_albums']} albums and "
        f"{albums['declared_patterns']} patterns declared, "
        f"{albums['tracks_reflagged_from_album_rules']} tracks reflagged, "
        f"{len(albums['excluded_artists'])} non-artist folders excluded"
    )
    popularity = report["popularity"]
    if popularity["measured_file_loaded"]:
        lines.append(
            f"song popularity: {popularity['measured_songs']} of "
            f"{popularity['scored_songs']} songs measured "
            f"({popularity['measured_coverage_pct']}%), the rest inferred"
        )
    else:
        lines.append("song popularity: no measured data loaded; every score is inferred")
    collisions = report["identities"]["identity_collisions"]
    if collisions:
        lines.append(f"IDENTITY COLLISIONS: {len(collisions)} - review identities.yaml")
    return lines


def write_report(project: Project, report: dict, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
