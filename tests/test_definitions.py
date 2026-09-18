"""Structural checks on playlist definitions."""

from __future__ import annotations

from music_engine.definitions import DEFINITION_KEYS, definition_errors


def test_family_is_an_unknown_key():
    """No code reads `family`, so a definition carrying it is a stale key."""
    assert "family" not in DEFINITION_KEYS
    assert definition_errors({"name": "Rock", "family": "rock"}) == [
        "Playlist 'Rock' has unknown key 'family'"
    ]


def test_matcher_keys_are_accepted():
    definition = {
        "name": "Rock",
        "type": "genre",
        "match_any_tags": ["rock"],
        "exclude_flags": ["live"],
        "ordering": {"strategy": "interleaved", "seed": "rock-v1"},
        "balancing": {"max_share_per_artist": 0.25},
    }
    assert definition_errors(definition) == []
