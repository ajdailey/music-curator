from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping

from .core import Track

_ALBUM_STRATEGIES = {"album_order", "chronological_album", "album_grouped"}
_SHUFFLE_STRATEGIES = {"deterministic", "stable_shuffle"}
_INTERLEAVED_STRATEGIES = {"interleaved", "curated_sequence"}

KNOWN_STRATEGIES = _ALBUM_STRATEGIES | _SHUFFLE_STRATEGIES | _INTERLEAVED_STRATEGIES

#: The ``ordering`` keys ``sequence_tracks`` reads for each strategy. Album
#: strategies read no seed and apply no spacing.
_SPACED_KEYS = frozenset({"strategy", "seed", "min_same_song_gap"})
ORDERING_KEYS: dict[str, frozenset[str]] = {
    **dict.fromkeys(_ALBUM_STRATEGIES, frozenset({"strategy"})),
    **dict.fromkeys(_SHUFFLE_STRATEGIES, _SPACED_KEYS),
    **dict.fromkeys(
        _INTERLEAVED_STRATEGIES,
        _SPACED_KEYS | {"max_consecutive_artist", "max_consecutive_album"},
    ),
}


def _stable_rank(seed: str, track: Track) -> str:
    payload = f"{seed}|{track.artist}|{track.album}|{track.title}|{track.relative_path}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def album_order(tracks: Iterable[Track]) -> list[Track]:
    """Sort by artist, album, disc, track, title.

    A filename without a disc prefix sorts as disc 1, and one without a track
    number sorts after the numbered tracks of its disc.
    """
    return sorted(
        tracks,
        key=lambda t: (
            t.artist.casefold(),
            t.album.casefold(),
            t.disc_no if t.disc_no is not None else 1,
            t.track_no or 999,
            t.title.casefold(),
        ),
    )


def deterministic_order(tracks: Iterable[Track], seed: str) -> list[Track]:
    return sorted(tracks, key=lambda t: _stable_rank(seed, t))


def _track_priority(track: Track, seed: str, score: float = 0.0) -> tuple:
    """Prefer studio-like, better-known material, deterministically."""
    penalty = 0
    for flag in ("demo", "alternate", "compilation"):
        if flag in track.flags:
            penalty += 4
    if "live" in track.flags:
        penalty += 2
    return penalty, -score, _stable_rank(seed, track)


def interleaved_order(
    tracks: Iterable[Track],
    seed: str,
    max_consecutive_artist: int = 1,
    max_consecutive_album: int = 1,
    score_of=None,
) -> list[Track]:
    """Deterministically rotate active artists and albums.

    ``max_consecutive_artist`` is the longest run of one artist: an artist plays
    up to that many tracks in a row before the rotation moves on, so 1 means no
    artist plays back to back. ``max_consecutive_album`` bounds the run of one
    album the same way. An artist left as the only one with tracks plays out
    regardless.

    Exhausted artists are removed from the active cycle. This keeps generation
    linear enough for large catalogs instead of repeatedly scanning empty groups.
    """
    max_artist = max(1, max_consecutive_artist)
    max_album = max(1, max_consecutive_album)
    grouped: dict[tuple[str, str], list[Track]] = defaultdict(list)
    for track in tracks:
        grouped[(track.artist.casefold(), track.album.casefold())].append(track)

    artist_albums: dict[str, dict[str, deque[Track]]] = defaultdict(dict)
    score = score_of or (lambda _track: 0.0)
    for (artist_key, album_key), rows in grouped.items():
        rows.sort(key=lambda t: _track_priority(t, seed, score(t)))
        artist_albums[artist_key][album_key] = deque(rows)

    active = deque(sorted(
        artist_albums,
        key=lambda a: hashlib.sha256(f"{seed}|artist|{a}".encode()).hexdigest(),
    ))
    album_cycles: dict[str, deque[str]] = {}
    for artist_key, albums in artist_albums.items():
        album_cycles[artist_key] = deque(sorted(
            albums,
            key=lambda a: hashlib.sha256(
                f"{seed}|album|{artist_key}|{a}".encode()
            ).hexdigest(),
        ))

    result: list[Track] = []
    run_artist: str | None = None
    artist_run = 0
    run_album: tuple[str, str] | None = None
    album_run = 0
    deferred = 0

    while active:
        artist_key = active.popleft()
        albums = album_cycles[artist_key]

        # Remove empty albums from this artist's rotation.
        albums = deque(a for a in albums if artist_albums[artist_key][a])
        album_cycles[artist_key] = albums
        if not albums:
            continue

        if artist_key == run_artist and artist_run >= max_artist and active:
            active.append(artist_key)
            deferred += 1
            if deferred <= len(active):
                continue
        deferred = 0

        chosen_album = None
        for _ in range(len(albums)):
            album_key = albums[0]
            albums.rotate(-1)
            if (artist_key, album_key) != run_album or album_run < max_album:
                chosen_album = album_key
                break
        if chosen_album is None:
            chosen_album = albums[0]

        track = artist_albums[artist_key][chosen_album].popleft()
        result.append(track)
        artist_run = artist_run + 1 if artist_key == run_artist else 1
        run_artist = artist_key
        album_run = album_run + 1 if (artist_key, chosen_album) == run_album else 1
        run_album = (artist_key, chosen_album)

        if any(artist_albums[artist_key][a] for a in albums):
            if artist_run < max_artist:
                active.appendleft(artist_key)
            else:
                active.append(artist_key)

    return result


def space_duplicates(tracks: Iterable[Track], min_gap: int) -> list[Track]:
    """Push recordings of the same song apart in the running order.

    Balancing collapses duplicate versions, but a playlist can still legitimately
    hold a studio take and a live take of one song, and a cover sits under the
    same title as the original. Hearing them back to back is jarring.

    Walks the sequence in order and defers any track whose song appeared within
    the last ``min_gap`` positions, placing the earliest eligible deferred track
    instead. Order is otherwise preserved, so the result stays deterministic. If
    nothing is eligible the oldest deferred track is placed anyway, which
    guarantees progress and termination.
    """
    rows = list(tracks)
    if min_gap < 1 or len(rows) < 2:
        return rows

    from .versions import base_title  # local import avoids a cycle

    def song_of(track: Track) -> tuple[str, str]:
        return (track.artist.casefold(), base_title(track.title))

    result: list[Track] = []
    deferred: list[Track] = []
    recent: deque[tuple[str, str]] = deque(maxlen=min_gap)
    index = 0

    while index < len(rows) or deferred:
        placed: Track | None = None

        for position, candidate in enumerate(deferred):
            if song_of(candidate) not in recent:
                placed = deferred.pop(position)
                break

        if placed is None:
            while index < len(rows):
                candidate = rows[index]
                index += 1
                if song_of(candidate) not in recent:
                    placed = candidate
                    break
                deferred.append(candidate)

        if placed is None:
            if not deferred:
                break
            # Nothing is eligible; place the longest-waiting track regardless so
            # the loop always terminates.
            placed = deferred.pop(0)

        result.append(placed)
        recent.append(song_of(placed))

    return result


def sequence_tracks(tracks: Iterable[Track], definition: Mapping, score_of=None) -> list[Track]:
    ordering = definition.get("ordering", {}) or {}
    strategy = ordering.get("strategy", "album_order")
    seed = str(ordering.get("seed", definition.get("name", "playlist")))

    if strategy in _ALBUM_STRATEGIES:
        # Album order is chronology within a record; spacing would break it.
        return album_order(tracks)
    if strategy in _SHUFFLE_STRATEGIES:
        ordered = deterministic_order(tracks, seed)
    elif strategy in _INTERLEAVED_STRATEGIES:
        ordered = interleaved_order(
            tracks,
            seed=seed,
            max_consecutive_artist=int(ordering.get("max_consecutive_artist", 1)),
            max_consecutive_album=int(ordering.get("max_consecutive_album", 1)),
            score_of=score_of,
        )
    else:
        raise ValueError(f"Unsupported ordering strategy: {strategy}")

    return space_duplicates(ordered, int(ordering.get("min_same_song_gap", 25)))
