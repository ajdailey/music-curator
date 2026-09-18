"""Artist identity, alias, and related-identity resolution."""

from __future__ import annotations

from music_engine.identity import (
    Identity,
    IdentityResolver,
    punctuation_key,
    unresolved_identities,
)


def test_punctuation_key_collapses_dotted_initialisms():
    # The R.E.M. defect: the catalog stores the filesystem-sanitized directory
    # name because Windows forbids a trailing dot.
    assert punctuation_key("R.E.M.") == punctuation_key("R.E.M_") == "rem"
    assert punctuation_key("REM") == "rem"


def test_punctuation_key_spells_out_ampersand():
    assert punctuation_key("Simon & Garfunkel") == punctuation_key("Simon and Garfunkel")


def test_punctuation_key_preserves_leading_article():
    # Dropping "The" would merge genuinely distinct acts.
    assert punctuation_key("The Band") != punctuation_key("Band")


def test_punctuation_key_ignores_diacritics():
    assert punctuation_key("H\u00fcsker D\u00fc") == punctuation_key("Husker Du")


def test_resolves_catalog_spelling_to_declared_identity():
    resolver = IdentityResolver.from_config(
        {"identities": {"R.E.M.": {"aliases": ["R.E.M_"]}}}
    )
    assert resolver.resolve("R.E.M_") == "R.E.M."
    assert resolver.resolve("r.e.m.") == "R.E.M."
    assert resolver.resolve("REM") == "R.E.M."


def test_undeclared_punctuation_variant_still_resolves():
    """Punctuation matching works without an explicit alias entry."""
    resolver = IdentityResolver.from_config({"identities": {"R.E.M.": {}}})
    assert resolver.resolve("R.E.M_") == "R.E.M."


def test_unknown_artist_resolves_to_itself():
    resolver = IdentityResolver()
    assert resolver.resolve("Nobody At All") == "Nobody At All"


def test_declared_identity_wins_over_observed_spelling():
    resolver = IdentityResolver.from_config(
        {"identities": {"R.E.M.": {"aliases": ["R.E.M_"]}}}
    )
    resolver.register_observed(["R.E.M_", "Wilco"])
    assert resolver.resolve("R.E.M_") == "R.E.M."
    assert resolver.resolve("Wilco") == "Wilco"


def test_related_identities_are_not_merged_automatically():
    resolver = IdentityResolver.from_config(
        {"identities": {"Neil Young": {"related": ["Crazy Horse"]}}}
    )
    # Crazy Horse stays its own identity...
    assert resolver.resolve("Crazy Horse") == "Crazy Horse"
    # ...and is only pulled in when a playlist opts in.
    assert resolver.expand(["Neil Young"], include_related=False) == {"Neil Young"}
    assert resolver.expand(["Neil Young"], include_related=True) == {
        "Neil Young",
        "Crazy Horse",
    }


def test_distinct_musicians_sharing_a_name_stay_separate():
    resolver = IdentityResolver.from_config(
        {"identities": {"Bill Evans": {}, "Bill Evans (sax)": {}}}
    )
    assert resolver.resolve("Bill Evans") != resolver.resolve("Bill Evans (sax)")
    assert resolver.collisions == []


def test_ambiguous_aliases_are_reported_not_merged():
    resolver = IdentityResolver()
    resolver.add(Identity(canonical="REM"))
    resolver.add(Identity(canonical="R.E.M."))
    assert [c.key for c in resolver.collisions] == ["rem"]


def test_unresolved_identities_flags_declared_but_absent_artists():
    resolver = IdentityResolver.from_config({"identities": {"Glenn Miller": {}}})
    missing = unresolved_identities(resolver, ["Glenn Miller"], ["Duke Ellington"])
    assert missing == ["Glenn Miller"]


def test_observed_punctuation_variant_resolves_to_the_declared_identity():
    """Regression: a catalog spelling differing only in punctuation became its
    own identity and recorded a collision against the declared one."""
    resolver = IdentityResolver.from_config({"identities": {"R.E.M.": {}}})
    resolver.register_observed(["R.E.M_"])
    assert resolver.resolve("R.E.M_") == "R.E.M."
    assert "R.E.M_" not in resolver.identities
    assert resolver.collisions == []


def test_observed_spellings_without_a_declared_identity_stay_distinct_keys():
    resolver = IdentityResolver()
    resolver.register_observed(["AC_DC", "AC/DC"])
    assert resolver.resolve("AC_DC") == "AC_DC"
    assert resolver.collisions  # two observed spellings still report ambiguity
