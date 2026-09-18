"""Plex synchronization.

Plex is a destination, never a source. Nothing in this package affects
playlist generation, taxonomy, sequencing, or catalog data.
"""

from .config import (
    MANAGED_PREFIX,
    PlexConfigurationError,
    PlexSettings,
)
from .models import ManifestEntry, PlexTrack, Resolution, ResolutionSummary
from .resolver import PlexResolver
from .synchronizer import PlexSynchronizer, SyncThresholds, is_managed, managed_title

__all__ = [
    "MANAGED_PREFIX",
    "ManifestEntry",
    "PlexConfigurationError",
    "PlexResolver",
    "PlexSettings",
    "PlexSynchronizer",
    "PlexTrack",
    "Resolution",
    "ResolutionSummary",
    "SyncThresholds",
    "is_managed",
    "managed_title",
]
