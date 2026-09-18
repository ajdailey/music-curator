"""Last.fm enrichment.

Strictly opt-in. Nothing here runs unless it is invoked explicitly and an API
key is configured. It is never part of generate, report, or the Plex sync flow.

Its only job is to fill the small gap Plex leaves: roughly 5% to 15% of songs
have no listener count in Plex metadata. Last.fm is rate limited to 5 requests
per second, so a full backfill is far slower than the Plex index and is only
worth running for the gap.
"""

from .config import ENV_API_KEY, LastfmConfigurationError, LastfmSettings

__all__ = ["ENV_API_KEY", "LastfmConfigurationError", "LastfmSettings"]
