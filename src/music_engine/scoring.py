"""Song prominence scoring.

A 2,000-track portable playlist must contain an artist's signature songs, not
an arbitrary slice of their catalog. Nothing in the library export states which
songs matter, so prominence is inferred from how the collection is shaped.

The primary signal is **re-release count**: how many distinct albums a song
appears on. Hits are re-released repeatedly onto greatest-hits sets, remasters,
deluxe editions, live records, and box sets, while deep cuts appear once or
twice. Measured against the real catalog, Stone Temple Pilots' most-repeated
songs are Vasoline, Plush, Creep, Big Empty, Sex Type Thing, and Interstate
Love Song, with album tracks like Meatplow and Piece of Pie well below.

Scores are normalized within each artist, so a prolific artist's deep cut
never outranks a smaller artist's biggest song.

Curated overrides always win: `essential` and `demote` lists in
`profiles/artists.yaml` state prominence explicitly where the heuristic is
wrong.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from .core import Track
from .versions import base_title, classify

#: A song named in a curated `essential` list scores at least this.
ESSENTIAL_SCORE = 1.0
#: A song named in a curated `demote` list scores at most this.
DEMOTED_SCORE = 0.0
#: Weight given to appearances on compilations relative to ordinary albums.
#: A compilation appearance is a stronger prominence signal than an album track.
COMPILATION_WEIGHT = 2.0


@dataclass
class PopularityIndex:
    """Per-song prominence, normalized within each artist."""

    scores: dict[tuple[str, str], float] = field(default_factory=dict)
    appearances: dict[tuple[str, str], int] = field(default_factory=dict)
    #: Songs whose score came from measured listener data rather than the
    #: re-release heuristic.
    measured: set[tuple[str, str]] = field(default_factory=set)

    @property
    def measured_coverage(self) -> float:
        return len(self.measured) / len(self.scores) if self.scores else 0.0

    def score(self, identity: str, title: str) -> float:
        return self.scores.get((identity.casefold(), base_title(title)), 0.0)

    def score_of(self, track: Track, identity_of: Mapping[str, str] | None = None) -> float:
        identity = (identity_of or {}).get(track.artist, track.artist)
        return self.score(identity, track.title)

    def top_songs(self, identity: str, limit: int = 10) -> list[tuple[str, float]]:
        rows = [
            (title, score)
            for (artist, title), score in self.scores.items()
            if artist == identity.casefold()
        ]
        rows.sort(key=lambda row: (-row[1], row[0]))
        return rows[:limit]


#: Songs with no measured listener count fall back to the re-release
#: heuristic, capped so a guess never outranks a song with real data.
UNMEASURED_CEILING = 0.5


def build_popularity(
    catalog: Iterable[Track],
    identity_of: Mapping[str, str] | None = None,
    curated: Mapping[str, Mapping] | None = None,
    measured=None,
    resolve_name: Callable[[str], str] | None = None,
) -> PopularityIndex:
    """Score every song, preferring measured data over the heuristic.

    ``measured`` is a PopularityData carrying real listener counts (see
    popularity.py). Where a song has one, it is used directly. Where it does
    not, the re-release heuristic stands in, capped at UNMEASURED_CEILING so an
    inferred score never beats a measured one.

    ``curated`` is keyed by profile artist name. ``resolve_name`` maps that name
    to its identity (``IdentityResolver.resolve``); without it the name is
    looked up in ``identity_of``.
    """
    resolve = (identity_of or {}).get

    weighted: dict[tuple[str, str], float] = defaultdict(float)
    albums_seen: dict[tuple[str, str], set[str]] = defaultdict(set)

    for track in catalog:
        identity = resolve(track.artist, track.artist).casefold()
        key = (identity, base_title(track.title))
        album = track.album.casefold()
        if album in albums_seen[key]:
            continue  # count each album once, not once per copy
        albums_seen[key].add(album)
        weight = (
            COMPILATION_WEIGHT
            if classify(track).release == "compilation"
            else 1.0
        )
        weighted[key] += weight

    # Normalize within each artist so scores are comparable across artists.
    best_per_artist: dict[str, float] = defaultdict(float)
    for (identity, _), value in weighted.items():
        best_per_artist[identity] = max(best_per_artist[identity], value)

    # Measured listener counts, normalized within each artist.
    best_measured: dict[str, int] = defaultdict(int)
    measured_value: dict[tuple[str, str], int] = {}
    if measured is not None:
        for identity, title in weighted:
            listeners = measured.listeners(identity, title)
            if listeners:
                measured_value[(identity, title)] = listeners
                best_measured[identity] = max(best_measured[identity], listeners)

    index = PopularityIndex()
    for key, value in weighted.items():
        identity = key[0]
        listeners = measured_value.get(key)
        if listeners and best_measured[identity]:
            index.scores[key] = listeners / best_measured[identity]
            index.measured.add(key)
        else:
            ceiling = best_per_artist[identity] or 1.0
            index.scores[key] = (value / ceiling) * UNMEASURED_CEILING
        index.appearances[key] = len(albums_seen[key])

    if resolve_name is None:
        lookup = identity_of or {}

        def resolve_name(name: str) -> str:
            return lookup.get(name, name)

    _apply_curated(index, curated or {}, resolve_name)
    return index


def _apply_curated(
    index: PopularityIndex,
    curated: Mapping[str, Mapping],
    resolve_name: Callable[[str], str],
) -> None:
    """Curated prominence overrides the heuristic."""
    for name, profile in curated.items():
        identity = resolve_name(str(name)).casefold()
        for title in (profile or {}).get("essential", []) or []:
            index.scores[(identity, base_title(str(title)))] = ESSENTIAL_SCORE
        for title in (profile or {}).get("demote", []) or []:
            index.scores[(identity, base_title(str(title)))] = DEMOTED_SCORE


def prominence_report(
    index: PopularityIndex, identities: Iterable[str], limit: int = 5
) -> list[dict]:
    """Top songs per artist, so the inferred prominence can be sanity-checked."""
    rows = []
    for identity in identities:
        top = index.top_songs(identity, limit)
        if top:
            rows.append(
                {
                    "artist": identity,
                    "top_songs": [
                        {"title": title, "score": round(score, 3)} for title, score in top
                    ],
                }
            )
    return rows
