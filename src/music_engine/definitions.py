"""Structural checks on playlist definitions.

An unknown key is an error, not a warning: ``match_any_tag`` for
``match_any_tags`` leaves the playlist with no tag criterion, which selects the
whole catalog. ``validate`` reports these errors and ``Project.load`` raises on
them, so generation never runs a misspelled definition.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping

from .balancing import BalanceConfig
from .core import safe_name
from .sequencing import ORDERING_KEYS

#: Top-level keys read by PlaylistMatcher and the pipeline.
DEFINITION_KEYS = frozenset(
    {
        "name",
        "type",
        "targets",
        "include_artists",
        "include_related",
        "exclude_artists",
        "match_any_tags",
        "match_any_genres",
        "match_all_tags",
        "exclude_flags",
        "ordering",
        "balancing",
    }
)

#: Keys BalanceConfig.from_definition reads.
BALANCING_KEYS = frozenset(f.name for f in dataclasses.fields(BalanceConfig))


def _unknown(keys: Iterable, allowed: frozenset[str]) -> list[str]:
    return sorted(str(k) for k in keys if str(k) not in allowed)


def definition_errors(definition: Mapping) -> list[str]:
    """Unknown keys in one definition and in its ordering and balancing blocks."""
    name = str(definition.get("name", "<unnamed>"))
    errors = [
        f"Playlist '{name}' has unknown key '{key}'"
        for key in _unknown(definition, DEFINITION_KEYS)
    ]
    ordering = definition.get("ordering") or {}
    strategy = str(ordering.get("strategy", "album_order"))
    allowed = ORDERING_KEYS.get(strategy)
    if allowed is not None:
        errors.extend(
            f"Playlist '{name}' has ordering key '{key}', which strategy "
            f"'{strategy}' does not read"
            for key in _unknown(ordering, allowed)
        )
    errors.extend(
        f"Playlist '{name}' has unknown balancing key '{key}'"
        for key in _unknown(definition.get("balancing") or {}, BALANCING_KEYS)
    )
    return errors


def filename_collisions(definitions: Iterable[Mapping]) -> list[str]:
    """Distinct playlist names that write the same output file."""
    by_file: dict[str, list[str]] = {}
    for definition in definitions:
        name = str(definition.get("name", ""))
        if name:
            by_file.setdefault(safe_name(name).casefold(), []).append(name)
    return [
        f"Playlists {', '.join(repr(n) for n in names)} share the output filename "
        f"'{safe_name(names[0])}.m3u8'"
        for _, names in sorted(by_file.items())
        if len(set(names)) > 1
    ]


def all_definition_errors(definitions: Iterable[Mapping]) -> list[str]:
    rows = list(definitions)
    errors: list[str] = []
    for definition in rows:
        errors.extend(definition_errors(definition))
    errors.extend(filename_collisions(rows))
    return errors
