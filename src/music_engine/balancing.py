"""Candidate balancing - composition control, applied before sequencing.

Sequencing only reorders what it is given. Balancing decides what is in the
playlist at all, so a genre playlist is not dominated by whichever artist owns
hundreds of live recordings.

Every operation is deterministic: candidates are ranked by version preference
with a stable hash tiebreak, then trimmed. The same catalog always produces the
same playlist.

Balancing is opt-in per playlist. Artist collections must leave it disabled so
complete discographies are preserved (Musical Constitution #1).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .core import Track
from .versions import classify, preference_key, select_versions


@dataclass(frozen=True)
class BalanceConfig:
    """Per-playlist balancing configuration."""

    enabled: bool = False
    max_tracks_per_artist: int | None = None
    max_share_per_artist: float | None = None
    min_tracks_per_artist: int = 1
    cornerstone_multiplier: float = 1.0
    max_live_share: float | None = None
    max_remix_share: float | None = None
    prefer_studio: bool = True
    dedupe_versions: bool = True
    max_versions_per_song: int = 1
    max_live_versions_per_song: int = 1
    seed: str = "balance"

    @classmethod
    def from_definition(cls, definition: Mapping, defaults: Mapping | None = None) -> BalanceConfig:
        merged: dict = dict(defaults or {})
        merged.update(definition.get("balancing") or {})
        if not merged:
            return cls(enabled=False, seed=str(definition.get("name", "balance")))

        def _opt_int(key):
            value = merged.get(key)
            return None if value is None else int(value)

        def _opt_float(key):
            value = merged.get(key)
            return None if value is None else float(value)

        return cls(
            enabled=bool(merged.get("enabled", False)),
            max_tracks_per_artist=_opt_int("max_tracks_per_artist"),
            max_share_per_artist=_opt_float("max_share_per_artist"),
            min_tracks_per_artist=int(merged.get("min_tracks_per_artist", 1)),
            cornerstone_multiplier=float(merged.get("cornerstone_multiplier", 1.0)),
            max_live_share=_opt_float("max_live_share"),
            max_remix_share=_opt_float("max_remix_share"),
            prefer_studio=bool(merged.get("prefer_studio", True)),
            dedupe_versions=bool(merged.get("dedupe_versions", True)),
            max_versions_per_song=int(merged.get("max_versions_per_song", 1)),
            max_live_versions_per_song=int(merged.get("max_live_versions_per_song", 1)),
            seed=str(merged.get("seed", definition.get("name", "balance"))),
        )


@dataclass
class BalanceReport:
    """What balancing actually did, for auditability."""

    input_tracks: int = 0
    output_tracks: int = 0
    removed_duplicates: int = 0
    removed_artist_cap: int = 0
    removed_live_quota: int = 0
    removed_remix_quota: int = 0
    capped_artists: dict[str, dict[str, int]] = field(default_factory=dict)
    live_share_before: float = 0.0
    live_share_after: float = 0.0
    remix_share_before: float = 0.0
    remix_share_after: float = 0.0

    def as_dict(self) -> dict:
        return {
            "input_tracks": self.input_tracks,
            "output_tracks": self.output_tracks,
            "removed_duplicates": self.removed_duplicates,
            "removed_artist_cap": self.removed_artist_cap,
            "removed_live_quota": self.removed_live_quota,
            "removed_remix_quota": self.removed_remix_quota,
            "capped_artists": self.capped_artists,
            "live_share_before": round(self.live_share_before, 4),
            "live_share_after": round(self.live_share_after, 4),
            "remix_share_before": round(self.remix_share_before, 4),
            "remix_share_after": round(self.remix_share_after, 4),
        }


def _live_share(tracks: Sequence[Track]) -> float:
    if not tracks:
        return 0.0
    return sum(1 for t in tracks if classify(t).is_live) / len(tracks)


def _remix_share(tracks: Sequence[Track]) -> float:
    if not tracks:
        return 0.0
    return sum(1 for t in tracks if classify(t).is_remix) / len(tracks)


def quota_allowance(others: int, share: float) -> int | None:
    """How many quota-limited tracks may survive beside ``others``, or None.

    Solves ``limited <= share * (others + limited)`` for ``limited``. The quota
    constrains the FINAL playlist, whose size depends on how many limited tracks
    survive; using ``share * current_size`` instead would leave the result above
    quota once the dropped tracks are gone. None means no limit.
    """
    if share >= 1.0:
        return None
    if share <= 0.0:
        return 0
    return math.floor(share * others / (1.0 - share))


def _apply_quota(tracks: Sequence[Track], share: float, is_limited) -> tuple[list[Track], int]:
    """Drop the least-preferred limited tracks until the quota is met.

    ``tracks`` must already be in preference order, so the survivors are the
    best of their kind rather than an arbitrary slice.
    """
    limited = [t for t in tracks if is_limited(t)]
    others = [t for t in tracks if not is_limited(t)]
    allowed = quota_allowance(len(others), share)
    if allowed is None or len(limited) <= allowed:
        return list(tracks), 0
    removed = len(limited) - allowed
    return others + limited[:allowed], removed


def solve_share_cap(available: Sequence[int], share: float) -> int:
    """Per-artist cap: no artist may contribute more than ``share`` of the pool.

    The share is measured against the candidate pool, not the finished
    playlist. Constraining the finished playlist is self-referential, because
    the playlist size depends on the cap, and solving it exactly drags every
    artist down to the smallest one whenever a playlist has few artists: Outlaw
    Country (four artists, the smallest holding 42 tracks) would keep 168 of
    3,260 tracks. The finished playlist may sit slightly above the share; the
    domination section of the report states the actual figure.

    A cap that lowers no artist's share of the result is not applied, and the
    largest count is returned instead: a single-artist playlist, or fewer than
    ``1 / share`` artists of equal size.
    """
    if not available or share <= 0:
        return max(available or [0])
    total = sum(available)
    cap = max(1, math.ceil(share * total))
    if cap >= max(available):
        return cap
    kept = [min(count, cap) for count in available]
    kept_total = sum(kept)
    # Integer form of kept / kept_total < count / total.
    if not any(k * total < count * kept_total for k, count in zip(kept, available, strict=True)):
        return max(available)
    return cap


def _artist_cap(
    identity: str,
    share_cap: int | None,
    config: BalanceConfig,
    cornerstones: set[str],
) -> int | None:
    """Soft cap for one artist, or None when uncapped."""
    caps: list[int] = []
    if config.max_tracks_per_artist is not None:
        caps.append(config.max_tracks_per_artist)
    if share_cap is not None:
        caps.append(share_cap)
    if not caps:
        return None
    cap = min(caps)
    if identity in cornerstones and config.cornerstone_multiplier != 1.0:
        cap = math.ceil(cap * config.cornerstone_multiplier)
    # The cap is soft: never drop an artist below the floor entirely.
    return max(cap, config.min_tracks_per_artist)


def balance_candidates(
    tracks: Iterable[Track],
    config: BalanceConfig,
    identity_of: Mapping[str, str] | None = None,
    cornerstones: Iterable[str] = (),
    score_of=None,
) -> tuple[list[Track], BalanceReport]:
    """Trim candidates to a balanced, deterministic composition."""
    rows = list(tracks)
    report = BalanceReport(input_tracks=len(rows))
    report.live_share_before = _live_share(rows)
    report.remix_share_before = _remix_share(rows)

    if not config.enabled or not rows:
        report.output_tracks = len(rows)
        report.live_share_after = report.live_share_before
        report.remix_share_after = report.remix_share_before
        return rows, report

    identity_map = dict(identity_of or {})
    cornerstone_set = set(cornerstones)
    # Artist caps must keep an artist's best-known songs, not an arbitrary slice.
    score = score_of or (lambda _track: 0.0)

    def identity(track: Track) -> str:
        return identity_map.get(track.artist, track.artist)

    # 1. Collapse duplicate recordings of the same song.
    if config.dedupe_versions:
        selection = select_versions(
            rows,
            identity_of=identity_map,
            seed=config.seed,
            prefer_studio=config.prefer_studio,
            max_live_versions_per_song=config.max_live_versions_per_song,
            max_versions_per_song=config.max_versions_per_song,
        )
        report.removed_duplicates = selection.removed
        rows = selection.tracks

    # 2. Rank deterministically so caps keep the best material.
    rows.sort(key=lambda t: preference_key(t, config.seed, config.prefer_studio, score(t)))

    # 3. Apply per-artist soft caps in one pass; the share cap is measured
    #    against the pool, so it does not depend on the result.
    by_artist: dict[str, list[Track]] = defaultdict(list)
    for track in rows:
        by_artist[identity(track)].append(track)

    share_cap: int | None = None
    if config.max_share_per_artist is not None:
        share_cap = solve_share_cap(
            [len(group) for group in by_artist.values()], config.max_share_per_artist
        )

    capped: list[Track] = []
    for name in sorted(by_artist):
        group = by_artist[name]
        cap = _artist_cap(name, share_cap, config, cornerstone_set)
        if cap is None or len(group) <= cap:
            capped.extend(group)
            continue
        report.capped_artists[name] = {"available": len(group), "kept": cap}
        report.removed_artist_cap += len(group) - cap
        capped.extend(group[:cap])

    capped.sort(key=lambda t: preference_key(t, config.seed, config.prefer_studio, score(t)))

    # 4. Enforce the composition quotas by dropping the least-preferred tracks
    #    of each kind. Version collapsing already removed the remixes that had
    #    an original to lose to, so a remix reaching here is a song the catalog
    #    holds only as a remix. Trimming one kind shrinks the playlist and can
    #    push the other over its share, so both quotas are reapplied until
    #    neither removes anything. Each pass only removes, so the loop ends.
    quotas = [
        (config.max_remix_share, lambda t: classify(t).is_remix, "removed_remix_quota"),
        (config.max_live_share, lambda t: classify(t).is_live, "removed_live_quota"),
    ]
    quotas = [quota for quota in quotas if quota[0] is not None]
    changed = bool(quotas)
    while changed and capped:
        changed = False
        for share, is_limited, counter in quotas:
            capped, removed = _apply_quota(capped, share, is_limited)
            if removed:
                changed = True
                setattr(report, counter, getattr(report, counter) + removed)
                capped.sort(
                    key=lambda t: preference_key(
                        t, config.seed, config.prefer_studio, score(t)
                    )
                )

    report.output_tracks = len(capped)
    report.live_share_after = _live_share(capped)
    report.remix_share_after = _remix_share(capped)
    return capped, report
