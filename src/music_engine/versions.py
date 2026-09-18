"""Recording-version classification and duplicate selection.

The catalog contains the same song many times over: studio originals, remasters,
deluxe-edition bonus discs, box sets, greatest-hits repackages, demos, alternate
takes, radio edits, remixes, and a very large body of live recordings.

Two separate operations:

* ``classify`` - determine the performance kind and release context
* ``select_versions`` - choose which of many recordings of one song to keep

Selection is deterministic: ties break on a stable hash of the track identity,
never on catalog row order.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .core import Track

# --- performance kinds (what was played) ---------------------------------
STUDIO = "studio"
LIVE = "live"
DEMO = "demo"
ALTERNATE = "alternate"
RADIO_EDIT = "radio-edit"
REMIX = "remix"

# --- release contexts (where it was published) ---------------------------
ORIGINAL = "original"
REMASTER = "remaster"
EXPANDED = "expanded"
DELUXE = "deluxe"
BOX_SET = "box-set"
COMPILATION = "compilation"

#: Names an album entry may list in ``clear_flags``: every performance kind
#: except studio, every release context except original, and the catalog's
#: ``acoustic`` flag.
KNOWN_FLAGS = frozenset(
    {LIVE, DEMO, ALTERNATE, RADIO_EDIT, REMIX, REMASTER, EXPANDED, DELUXE, BOX_SET,
     COMPILATION, "acoustic"}
)

# A remix sorts last. It is not another performance of the song but another
# arrangement of it, so it should never stand in for the recording a listener
# means when they name the song.
_PERFORMANCE_ORDER = {STUDIO: 0, RADIO_EDIT: 1, LIVE: 2, ALTERNATE: 3, DEMO: 4, REMIX: 5}
_RELEASE_ORDER = {
    ORIGINAL: 0,
    REMASTER: 1,
    EXPANDED: 2,
    DELUXE: 3,
    BOX_SET: 4,
    COMPILATION: 5,
}

# Word boundaries that treat "_" as a separator. Filenames store a colon as
# "_" ("Live_ London 2013"), and "_" is a regex word character, so `\b` finds
# no boundary between "Live" and "_".
_START = r"(?<![^\W_])"
_END = r"(?![^\W_])"


def marker(body: str, flags: int = 0) -> re.Pattern[str]:
    """Compile ``body`` as a case-insensitive whole-word version marker."""
    return re.compile(rf"{_START}(?:{body}){_END}", re.IGNORECASE | flags)


# Deliberately narrow. "Remix" and its spellings are unambiguous, and a bare
# "Mix" is not: "Original Mix", "Mono Mix", and "Final Mix" are the record as
# released, not a reworking of it. Only mixes named for a dance-floor edit are
# matched, so the false-positive cost of demoting a track stays near zero.
# Shared with catalog.derive_flags so the `remix` flag and the `remix`
# performance kind can never disagree.
REMIX_TEXT = marker(
    r"""
          re-?mix(?:e[sd])?
        | (?:club|dance|dub|house|techno|electro|disco|extended|party)\s+mix
        | (?:12|7)\s*(?:inch|")\s*mix
    """,
    re.VERBOSE,
)

# "Live and Let Die" and "Live Wire" are song titles, not live markers. Shared
# with catalog.derive_flags.
LIVE_TEXT = marker(rf"live{_END}(?!\s+(?:and\s+let|wire{_END}))")
_DEMO_TEXT = marker(r"demo")
_ALTERNATE_TEXT = marker(r"(?:alternate|alt\.?)\s+(?:take|version|mix)")
_RADIO_TEXT = marker(r"radio\s+edit|single\s+version")
_REMASTER_TEXT = marker(r"remaster(?:ed)?")
_DELUXE_TEXT = marker(r"deluxe")
_EXPANDED_TEXT = marker(r"expanded|bonus")
_BOX_TEXT = marker(r"box\s*set")
# The Enja Heritage Collection reissues original albums under a series name.
_COMPILATION_TEXT = marker(
    r"greatest\s+hits|best\s+of|anthology|essential|(?<!heritage\s)collection|compilation"
)

# Parenthetical/suffix noise stripped when grouping versions of one song. A
# reprise or an instrumental is a different piece from the song it is named
# for, so a marker naming either is never stripped, even as "(Instrumental
# Version)".
_DISTINCT_PIECE = r"(?:reprise|instrumental)"
_VERSION_SUFFIX = re.compile(
    rf"""(
        \s*[\(\[](?![^\)\]]*{_DISTINCT_PIECE})[^\)\]]*(?:live|demo|remaster|alternate|
            version|edit|mix|take|mono|stereo|acoustic|bonus|session)[^\)\]]*[\)\]]
      | \s+-\s+(?!.*{_DISTINCT_PIECE})(?:live|demo|remaster(?:ed)?|alternate|single|radio|
            mono|stereo|acoustic){_END}.*$
      | \s+-\s+[^-]*{_START}re-?mix(?:e[sd])?{_END}.*$
      | \s+-\s+(?:[^-]*\s)?(?:club|dance|dub|house|techno|electro|disco|extended|party)
            \s+mix{_END}.*$
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_TRAILING_PUNCT = re.compile(r"[\s\-_.,;:]+$")


@dataclass(frozen=True)
class VersionInfo:
    """Classification of a single recording."""

    performance: str
    release: str

    @property
    def is_studio(self) -> bool:
        return self.performance == STUDIO

    @property
    def is_live(self) -> bool:
        return self.performance == LIVE

    @property
    def is_remix(self) -> bool:
        return self.performance == REMIX

    @property
    def rank(self) -> tuple[int, int]:
        """Lower sorts better."""
        return (
            _PERFORMANCE_ORDER.get(self.performance, 9),
            _RELEASE_ORDER.get(self.release, 9),
        )


def classify(track: Track) -> VersionInfo:
    """Classify a track from its catalog flags and its album and title text.

    Each kind matches on its flag or its text. Performance kinds are tested
    remix, live, demo, alternate, radio edit, else studio; release contexts
    compilation, box set, deluxe, expanded, remaster, else original. A kind in
    ``track.cleared_flags`` never matches.
    """
    flags = {f.casefold() for f in track.flags}
    cleared = track.cleared_flags
    haystack = f"{track.album} | {track.title}"

    def has(kind: str, pattern: re.Pattern[str] | None, text: str) -> bool:
        if kind in cleared:
            return False
        return kind in flags or bool(pattern and pattern.search(text))

    # Remix is tested first because it is the most specific signal: a remix
    # album is often also named for a venue or a year, and "Song (Live Dub Mix)"
    # is a reworking, not a concert recording.
    if has(REMIX, REMIX_TEXT, haystack):
        performance = REMIX
    elif has(LIVE, LIVE_TEXT, haystack):
        performance = LIVE
    elif has(DEMO, _DEMO_TEXT, haystack):
        performance = DEMO
    elif has(ALTERNATE, _ALTERNATE_TEXT, haystack):
        performance = ALTERNATE
    elif has(RADIO_EDIT, _RADIO_TEXT, haystack):
        performance = RADIO_EDIT
    else:
        performance = STUDIO

    if has(COMPILATION, _COMPILATION_TEXT, track.album):
        release = COMPILATION
    elif has(BOX_SET, _BOX_TEXT, track.album):
        release = BOX_SET
    elif has(DELUXE, _DELUXE_TEXT, track.album):
        release = DELUXE
    elif has(EXPANDED, _EXPANDED_TEXT, track.album):
        release = EXPANDED
    elif has(REMASTER, _REMASTER_TEXT, haystack):
        release = REMASTER
    else:
        release = ORIGINAL

    return VersionInfo(performance=performance, release=release)


def base_title(title: str) -> str:
    """Strip version markers so recordings of one song group together."""
    value = _VERSION_SUFFIX.sub("", title)
    value = _TRAILING_PUNCT.sub("", value)
    return (value or title).strip().casefold()


def song_key(track: Track, identity: str) -> tuple[str, str]:
    """Group key identifying 'the same song by the same artist'."""
    return (identity.casefold(), base_title(track.title))


def _tiebreak(track: Track, seed: str) -> str:
    payload = f"{seed}|{track.artist}|{track.album}|{track.title}|{track.relative_path}"
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def preference_key(
    track: Track,
    seed: str,
    prefer_studio: bool = True,
    score: float = 0.0,
) -> tuple:
    """Deterministic ordering key; lower is preferred.

    ``score`` is song prominence in 0..1. It is applied after recording quality
    so a signature song's bootleg never outranks a studio take, but among
    comparable recordings the better-known song wins.
    """
    info = classify(track)
    performance_rank, release_rank = info.rank
    if not prefer_studio:
        # prefer_studio governs the studio-versus-live tradeoff. A remix stays
        # demoted either way, since turning that preference off is a request for
        # live material, never a request for a remix to stand in for the song.
        performance_rank = _PERFORMANCE_ORDER[REMIX] if info.performance == REMIX else 0
    return (performance_rank, release_rank, -score, _tiebreak(track, seed))


@dataclass
class VersionSelection:
    """Result of collapsing duplicate recordings."""

    tracks: list[Track]
    removed: int
    songs: int
    live_kept: int


def select_versions(
    tracks: Iterable[Track],
    identity_of: Mapping[str, str] | None = None,
    seed: str = "versions",
    prefer_studio: bool = True,
    max_live_versions_per_song: int = 1,
    max_versions_per_song: int = 1,
) -> VersionSelection:
    """Collapse duplicate recordings of the same song.

    Keeps ``max_versions_per_song`` best versions, and additionally allows up to
    ``max_live_versions_per_song`` live recordings so meaningful live material
    survives (Musical Constitution #3).
    """
    rows = list(tracks)
    grouped: dict[tuple[str, str], list[Track]] = defaultdict(list)
    for track in rows:
        identity = (identity_of or {}).get(track.artist, track.artist)
        grouped[song_key(track, identity)].append(track)

    kept: list[Track] = []
    live_kept = 0
    for _, group in sorted(grouped.items()):
        group.sort(key=lambda t: preference_key(t, seed, prefer_studio))
        chosen = group[: max(1, max_versions_per_song)]
        chosen_ids = {id(t) for t in chosen}
        if max_live_versions_per_song > 0:
            live_extra = [
                t
                for t in group
                if id(t) not in chosen_ids and classify(t).is_live
            ][:max_live_versions_per_song]
            chosen.extend(live_extra)
        live_kept += sum(1 for t in chosen if classify(t).is_live)
        kept.extend(chosen)

    # Restore a deterministic global order independent of dict iteration.
    kept.sort(key=lambda t: preference_key(t, seed, prefer_studio))
    return VersionSelection(
        tracks=kept,
        removed=len(rows) - len(kept),
        songs=len(grouped),
        live_kept=live_kept,
    )


def version_statistics(tracks: Sequence[Track]) -> dict[str, dict[str, int]]:
    """Counts by performance kind and release context, for reporting."""
    performance: dict[str, int] = defaultdict(int)
    release: dict[str, int] = defaultdict(int)
    for track in tracks:
        info = classify(track)
        performance[info.performance] += 1
        release[info.release] += 1
    return {
        "performance": dict(sorted(performance.items())),
        "release": dict(sorted(release.items())),
    }
