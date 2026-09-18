"""Artist identity, alias, and related-identity resolution.

The catalog stores filesystem-sanitized artist names (``R.E.M_`` rather than
``R.E.M.``), so taxonomy files that spell an artist correctly cannot match by
string equality alone. This module resolves any spelling of an artist to a
single canonical identity using, in order of precedence:

1. exact (case-folded) match against the canonical name,
2. exact (case-folded) match against a declared alias,
3. punctuation-insensitive match against the canonical name or an alias.

Related identities (side projects, alternate billings) are never merged
automatically. They are declared explicitly and only applied by playlists that
opt in via ``include_related``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

_NON_ALPHANUMERIC = re.compile(r"[^0-9a-zA-Z]+", re.UNICODE)


def exact_key(name: str) -> str:
    """Case-folded key used for exact identity matching."""
    return unicodedata.normalize("NFC", name).strip().casefold()


def punctuation_key(name: str) -> str:
    """Punctuation-insensitive key.

    ``R.E.M.``, ``R.E.M_`` and ``REM`` all collapse to ``rem``. ``&`` is spelled
    out so ``Simon & Garfunkel`` and ``Simon and Garfunkel`` agree. Leading
    articles are deliberately preserved: dropping ``The`` would merge genuinely
    distinct acts such as ``The Band`` and ``Band``.
    """
    value = unicodedata.normalize("NFKD", name)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.replace("&", " and ")
    # Punctuation AND spacing are both dropped, so a dotted initialism collapses
    # to the same key as its unspaced form: R.E.M. -> rem, matching REM. Keeping
    # spaces would yield "r e m" and the alias would never match.
    return _NON_ALPHANUMERIC.sub("", value).casefold()


@dataclass(frozen=True)
class Identity:
    """A canonical artist identity."""

    canonical: str
    aliases: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    note: str = ""

    @property
    def spellings(self) -> tuple[str, ...]:
        """Every name that should resolve to this identity."""
        return (self.canonical, *self.aliases)


@dataclass
class IdentityCollision:
    """Two distinct identities that share a punctuation key."""

    key: str
    canonical: str
    other: str


@dataclass
class IdentityResolver:
    """Resolves arbitrary artist spellings to canonical identities."""

    identities: dict[str, Identity] = field(default_factory=dict)
    _exact: dict[str, str] = field(default_factory=dict, repr=False)
    _punct: dict[str, str] = field(default_factory=dict, repr=False)
    collisions: list[IdentityCollision] = field(default_factory=list)
    _observed: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def from_config(cls, config: Mapping) -> IdentityResolver:
        resolver = cls()
        for name, raw in (config.get("identities") or {}).items():
            if not isinstance(name, str):
                raise ValueError(
                    f"Identity key {name!r} is not a string. Quote YAML names "
                    "such as 'Yes', 'No', 'On', and 'Off'."
                )
            data = raw or {}
            identity = Identity(
                canonical=name,
                aliases=tuple(str(a) for a in data.get("aliases", [])),
                related=tuple(str(a) for a in data.get("related", [])),
                note=str(data.get("note", "")),
            )
            resolver.add(identity)
        return resolver

    def add(self, identity: Identity) -> None:
        self.identities[identity.canonical] = identity
        for spelling in identity.spellings:
            self._exact[exact_key(spelling)] = identity.canonical
        for spelling in identity.spellings:
            key = punctuation_key(spelling)
            if not key:
                continue
            existing = self._punct.get(key)
            if existing is None:
                self._punct[key] = identity.canonical
            elif existing != identity.canonical:
                # Ambiguous: report it rather than silently merging artists.
                self.collisions.append(
                    IdentityCollision(key=key, canonical=identity.canonical, other=existing)
                )

    def register_observed(self, names: Iterable[str]) -> None:
        """Register catalog spellings that have no declared identity.

        Every observed artist becomes resolvable, so downstream code can treat
        the resolver as total. Declared identities always win: a spelling whose
        punctuation key already resolves to a declared identity (``R.E.M_`` for
        a declared ``R.E.M.``) is not registered, so it resolves to that
        identity instead of becoming a second one.
        """
        for name in names:
            if exact_key(name) in self._exact:
                continue
            existing = self._punct.get(punctuation_key(name))
            if existing is not None and existing not in self._observed:
                continue
            self._observed.add(name)
            self.add(Identity(canonical=name))

    def resolve(self, name: str) -> str:
        """Return the canonical identity for ``name`` (itself if unknown)."""
        canonical = self._exact.get(exact_key(name))
        if canonical is not None:
            return canonical
        canonical = self._punct.get(punctuation_key(name))
        if canonical is not None:
            return canonical
        return name

    def is_declared(self, name: str) -> bool:
        return self.resolve(name) in self.identities

    def related_to(self, name: str) -> tuple[str, ...]:
        """Explicitly declared related identities, resolved to canonical form."""
        identity = self.identities.get(self.resolve(name))
        if identity is None:
            return ()
        return tuple(self.resolve(r) for r in identity.related)

    def expand(self, names: Iterable[str], include_related: bool = False) -> set[str]:
        """Expand names into the set of canonical identities they select."""
        selected: set[str] = set()
        for name in names:
            canonical = self.resolve(name)
            selected.add(canonical)
            if include_related:
                selected.update(self.related_to(canonical))
        return selected


def unresolved_identities(
    resolver: IdentityResolver, declared: Iterable[str], catalog_artists: Iterable[str]
) -> list[str]:
    """Declared names that match no artist present in the catalog."""
    present = {resolver.resolve(a) for a in catalog_artists}
    return sorted({n for n in declared if resolver.resolve(n) not in present})
