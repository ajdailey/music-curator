"""Auditing analytics: playlist overlap, artist domination, and coverage.

These functions report the taxonomy defects that would otherwise be found only
by reading 50 generated M3U files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .core import Track

# --------------------------------------------------------------------------
# Playlist overlap
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OverlapStats:
    left: str
    right: str
    left_tracks: int
    right_tracks: int
    shared_tracks: int
    left_only_tracks: int
    right_only_tracks: int
    shared_artists: int
    left_artists: int
    right_artists: int
    track_jaccard: float
    artist_jaccard: float
    overlap_of_smaller: float

    def as_dict(self) -> dict:
        return {
            "left": self.left,
            "right": self.right,
            "left_tracks": self.left_tracks,
            "right_tracks": self.right_tracks,
            "shared_tracks": self.shared_tracks,
            "left_only_tracks": self.left_only_tracks,
            "right_only_tracks": self.right_only_tracks,
            "left_artists": self.left_artists,
            "right_artists": self.right_artists,
            "shared_artists": self.shared_artists,
            "track_jaccard": round(self.track_jaccard, 4),
            "artist_jaccard": round(self.artist_jaccard, 4),
            "overlap_of_smaller_pct": round(100 * self.overlap_of_smaller, 2),
        }

    @property
    def is_effective_duplicate(self) -> bool:
        """Near-identical membership: two playlists this similar indicate a taxonomy defect."""
        return self.track_jaccard >= 0.9


def _jaccard(left: set, right: set) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def overlap(
    name_left: str,
    left: Sequence[Track],
    name_right: str,
    right: Sequence[Track],
    identity_of: Mapping[str, str] | None = None,
) -> OverlapStats:
    """Compare two playlists by track identity and artist identity."""
    resolve = (identity_of or {}).get
    left_keys = {t.key for t in left}
    right_keys = {t.key for t in right}
    left_artists = {resolve(t.artist, t.artist) for t in left}
    right_artists = {resolve(t.artist, t.artist) for t in right}

    shared = left_keys & right_keys
    smaller = min(len(left_keys), len(right_keys))
    return OverlapStats(
        left=name_left,
        right=name_right,
        left_tracks=len(left_keys),
        right_tracks=len(right_keys),
        shared_tracks=len(shared),
        left_only_tracks=len(left_keys - right_keys),
        right_only_tracks=len(right_keys - left_keys),
        shared_artists=len(left_artists & right_artists),
        left_artists=len(left_artists),
        right_artists=len(right_artists),
        track_jaccard=_jaccard(left_keys, right_keys),
        artist_jaccard=_jaccard(left_artists, right_artists),
        overlap_of_smaller=(len(shared) / smaller) if smaller else 0.0,
    )


def overlap_matrix(
    playlists: Mapping[str, Sequence[Track]],
    pairs: Iterable[tuple[str, str]],
    identity_of: Mapping[str, str] | None = None,
) -> list[dict]:
    """Overlap stats for the requested comparison pairs."""
    rows = []
    for left, right in pairs:
        if left not in playlists or right not in playlists:
            continue
        rows.append(
            overlap(left, playlists[left], right, playlists[right], identity_of).as_dict()
        )
    return rows


# --------------------------------------------------------------------------
# Artist domination
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DominationStats:
    playlist: str
    tracks: int
    distinct_artists: int
    effective_artists: float
    top_artist: str | None
    top_artist_share: float
    top_artists: list[dict]

    def as_dict(self) -> dict:
        return {
            "playlist": self.playlist,
            "tracks": self.tracks,
            "distinct_artists": self.distinct_artists,
            "effective_artists": round(self.effective_artists, 2),
            "top_artist": self.top_artist,
            "top_artist_share_pct": round(100 * self.top_artist_share, 2),
            "top_artists": self.top_artists,
        }

    @property
    def is_dominated(self) -> bool:
        return self.top_artist_share >= 0.25


def domination(
    name: str,
    tracks: Sequence[Track],
    identity_of: Mapping[str, str] | None = None,
    top_n: int = 10,
) -> DominationStats:
    """Artist concentration within one playlist.

    ``effective_artists`` is the inverse Simpson index (1 / sum of squared
    shares): the number of equally-sized artists that would produce the same
    concentration. A 40-artist playlist where one artist owns half the tracks
    has an effective diversity far below 40.
    """
    resolve = (identity_of or {}).get
    counts = Counter(resolve(t.artist, t.artist) for t in tracks)
    total = sum(counts.values())
    if not total:
        return DominationStats(name, 0, 0, 0.0, None, 0.0, [])

    concentration = sum((c / total) ** 2 for c in counts.values())
    effective = (1 / concentration) if concentration else 0.0
    top = counts.most_common(top_n)
    return DominationStats(
        playlist=name,
        tracks=total,
        distinct_artists=len(counts),
        effective_artists=effective,
        top_artist=top[0][0],
        top_artist_share=top[0][1] / total,
        top_artists=[
            {"artist": a, "tracks": c, "share_pct": round(100 * c / total, 2)}
            for a, c in top
        ],
    )


# --------------------------------------------------------------------------
# Source coverage
# --------------------------------------------------------------------------


def coverage(
    playlist: str,
    plex_tracks: Sequence[Track],
    rockbox_tracks: Sequence[Track],
    identity_of: Mapping[str, str] | None = None,
) -> dict:
    """How much of a Plex playlist survives into the portable library."""
    resolve = (identity_of or {}).get
    plex_artists = Counter(resolve(t.artist, t.artist) for t in plex_tracks)
    rockbox_artists = {resolve(t.artist, t.artist) for t in rockbox_tracks}
    missing = sorted(
        (a for a in plex_artists if a not in rockbox_artists),
        key=lambda a: -plex_artists[a],
    )
    ratio = (len(rockbox_tracks) / len(plex_tracks)) if plex_tracks else 0.0
    return {
        "playlist": playlist,
        "plex_tracks": len(plex_tracks),
        "rockbox_tracks": len(rockbox_tracks),
        "coverage_pct": round(100 * ratio, 2),
        "plex_artists": len(plex_artists),
        "rockbox_artists": len(rockbox_artists),
        "artists_absent_from_portable": len(missing),
        "top_absent_artists": [
            {"artist": a, "plex_tracks": plex_artists[a]} for a in missing[:10]
        ],
    }


def explain_zero(
    playlist: str,
    target: str,
    plex_tracks: Sequence[Track],
    source_tracks: Sequence[Track],
    matched_tracks: Sequence[Track],
    identity_of: Mapping[str, str] | None = None,
) -> dict:
    """Explain why a playlist produced no tracks for a target.

    Distinguishes the three causes that matter: nothing matches the taxonomy at
    all, the artists exist but none are present in the target source, or the
    source is fine and something downstream removed everything.
    """
    resolve = (identity_of or {}).get
    plex_artists = Counter(resolve(t.artist, t.artist) for t in plex_tracks)
    source_artists = {resolve(t.artist, t.artist) for t in source_tracks}
    present = sorted(a for a in plex_artists if a in source_artists)

    if not plex_artists:
        reason = "no_catalog_matches"
        detail = (
            "No track in the full catalog matches this definition. The tags or "
            "artist names it references are unpopulated."
        )
    elif not present:
        reason = "absent_from_source"
        detail = (
            f"{len(plex_artists)} matching artist(s) exist in the full catalog "
            f"but none are present in the '{target}' source. Genuine absence, "
            "not a defect."
        )
    elif not matched_tracks:
        reason = "removed_downstream"
        detail = (
            f"{len(present)} matching artist(s) are present in the '{target}' "
            "source, but no tracks survived filtering. Investigate."
        )
    else:
        reason = "not_zero"
        detail = "Playlist is not empty for this target."

    return {
        "playlist": playlist,
        "target": target,
        "reason": reason,
        "explanation": detail,
        "plex_candidates": len(plex_tracks),
        "source_candidates": len(source_tracks),
        "matched_candidates": len(matched_tracks),
        "matching_artists": len(plex_artists),
        "matching_artists_in_source": len(present),
        "example_artists": [
            {"artist": a, "plex_tracks": c} for a, c in plex_artists.most_common(5)
        ],
        "example_artists_in_source": present[:5],
        "example_unmatched_paths": [t.relative_path for t in plex_tracks[:3]],
    }
