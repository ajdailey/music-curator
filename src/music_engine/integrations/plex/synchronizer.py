"""Compare and synchronize generated playlists into Plex.

Safety rules, enforced here rather than left to the caller:

* Only playlists prefixed ``MC - `` are ever modified or deleted.
* Nothing is written without an explicit apply; dry run is the default.
* A playlist whose resolution quality is below threshold is refused.
* A populated playlist is never replaced by an empty one.
* A playlist is built and verified under a staging title before the swap, so a
  failure leaves the previous playlist intact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .config import MANAGED_PREFIX
from .models import ManifestEntry, PlaylistDiff, ResolutionSummary
from .resolver import PlexResolver


def managed_title(playlist: str) -> str:
    return f"{MANAGED_PREFIX}{playlist}"


def is_managed(title: str) -> bool:
    return title.startswith(MANAGED_PREFIX)


@dataclass(frozen=True)
class SyncThresholds:
    """Quality gates a playlist must clear before it is written."""

    max_unresolved_pct: float = 5.0
    max_ambiguous_pct: float = 2.0

    def evaluate(self, summary: ResolutionSummary) -> tuple[bool, str]:
        if summary.desired == 0:
            return False, "playlist is empty"
        unresolved_pct = 100 * summary.unresolved / summary.desired
        ambiguous_pct = 100 * summary.ambiguous / summary.desired
        if unresolved_pct > self.max_unresolved_pct:
            return False, (
                f"{summary.unresolved} unresolved ({unresolved_pct:.1f}%) "
                f"exceeds the {self.max_unresolved_pct}% threshold"
            )
        if ambiguous_pct > self.max_ambiguous_pct:
            return False, (
                f"{summary.ambiguous} ambiguous ({ambiguous_pct:.1f}%) "
                f"exceeds the {self.max_ambiguous_pct}% threshold"
            )
        if not summary.rating_keys:
            return False, "no tracks resolved"
        return True, "within thresholds"


def diff_playlist(
    playlist: str,
    summary: ResolutionSummary,
    actual_rating_keys: Sequence[str],
    exists: bool,
) -> PlaylistDiff:
    """Compare desired membership and order against what Plex currently holds."""
    desired_keys = summary.rating_keys
    desired_set, actual_set = set(desired_keys), set(actual_rating_keys)
    return PlaylistDiff(
        playlist=playlist,
        managed_title=managed_title(playlist),
        exists=exists,
        desired=len(desired_keys),
        actual=len(actual_rating_keys),
        to_add=len(desired_set - actual_set),
        to_remove=len(actual_set - desired_set),
        # Order matters: Plex must not be relied on to sort.
        reorder_required=exists and list(actual_rating_keys) != desired_keys,
        unresolved=summary.unresolved,
        ambiguous=summary.ambiguous,
    )


@dataclass
class SyncOutcome:
    """What synchronizing one playlist did, or would do."""

    playlist: str
    managed_title: str
    action: str  # create | replace | skip | up-to-date
    applied: bool
    reason: str = ""
    desired: int = 0
    previous: int = 0
    verified: bool = False

    def as_dict(self) -> dict:
        return {
            "playlist": self.playlist,
            "managed_title": self.managed_title,
            "action": self.action,
            "applied": self.applied,
            "reason": self.reason,
            "desired": self.desired,
            "previous": self.previous,
            "verified": self.verified,
        }


class PlexSynchronizer:
    """Drives resolution, diffing, and playlist writes."""

    def __init__(
        self,
        client,
        resolver: PlexResolver,
        thresholds: SyncThresholds | None = None,
    ):
        self.client = client
        self.resolver = resolver
        self.thresholds = thresholds or SyncThresholds()

    def resolve(self, playlist: str, entries: Sequence[ManifestEntry]) -> ResolutionSummary:
        return self.resolver.resolve(playlist, entries)

    def diff(
        self, playlist: str, entries: Sequence[ManifestEntry]
    ) -> tuple[PlaylistDiff, ResolutionSummary]:
        summary = self.resolve(playlist, entries)
        title = managed_title(playlist)
        existing = self.client.playlist(title)
        actual = self.client.playlist_items(title) if existing is not None else []
        return diff_playlist(playlist, summary, actual, existing is not None), summary

    def sync(
        self,
        playlist: str,
        entries: Sequence[ManifestEntry],
        apply: bool = False,
        create_only: bool = False,
    ) -> SyncOutcome:
        """Synchronize one playlist. Writes only when ``apply`` is true."""
        difference, summary = self.diff(playlist, entries)
        title = difference.managed_title

        acceptable, reason = self.thresholds.evaluate(summary)
        if not acceptable:
            return SyncOutcome(
                playlist=playlist,
                managed_title=title,
                action="skip",
                applied=False,
                reason=f"refused: {reason}",
                desired=difference.desired,
                previous=difference.actual,
            )

        if difference.exists and create_only:
            return SyncOutcome(
                playlist=playlist,
                managed_title=title,
                action="skip",
                applied=False,
                reason="exists and --create-only was given",
                desired=difference.desired,
                previous=difference.actual,
            )

        if difference.in_sync:
            return SyncOutcome(
                playlist=playlist,
                managed_title=title,
                action="up-to-date",
                applied=False,
                reason="membership and order already match",
                desired=difference.desired,
                previous=difference.actual,
                verified=True,
            )

        action = "replace" if difference.exists else "create"
        if not apply:
            return SyncOutcome(
                playlist=playlist,
                managed_title=title,
                action=action,
                applied=False,
                reason="dry run; pass --apply to write",
                desired=difference.desired,
                previous=difference.actual,
            )

        verified = self._write(title, summary.rating_keys)
        return SyncOutcome(
            playlist=playlist,
            managed_title=title,
            action=action,
            applied=True,
            reason="written and verified" if verified else "written but verification failed",
            desired=difference.desired,
            previous=difference.actual,
            verified=verified,
        )

    def _write(self, title: str, rating_keys: list[str]) -> bool:
        """Build ``<title> (syncing)``, verify it, then swap it into place.

        The swap renames the live playlist to ``<title> (previous)``, renames
        the staging copy to ``<title>``, and only then deletes the previous
        copy. An interruption at any step leaves either a verified ``<title>``
        or the previous playlist intact under one of the two names. A rerun
        deletes a leftover staging copy before building, and deletes a leftover
        previous copy once ``<title>`` is in place again.

        A failed create or a failed verification deletes the staging copy and
        leaves ``<title>`` untouched; the create error is re-raised.
        """
        if not is_managed(title):
            raise ValueError(f"Refusing to write unmanaged playlist {title!r}")
        if not rating_keys:
            raise ValueError(f"Refusing to write empty playlist {title!r}")

        staging_title = f"{title} (syncing)"
        previous_title = f"{title} (previous)"
        if self.client.playlist(staging_title) is not None:
            self.client.delete_playlist(staging_title)
        if (
            self.client.playlist(title) is not None
            and self.client.playlist(previous_title) is not None
        ):
            self.client.delete_playlist(previous_title)

        try:
            self.client.create_playlist(staging_title, rating_keys)
        except BaseException:
            self._discard(staging_title)
            raise
        if self.client.playlist_items(staging_title) != rating_keys:
            self.client.delete_playlist(staging_title)
            return False

        if self.client.playlist(title) is not None:
            self.client.rename_playlist(title, previous_title)
        self.client.rename_playlist(staging_title, title)
        if self.client.playlist(previous_title) is not None:
            self.client.delete_playlist(previous_title)
        return self.client.playlist_items(title) == rating_keys

    def _discard(self, title: str) -> None:
        """Delete a staging copy after a failure, keeping the original error."""
        try:
            if self.client.playlist(title) is not None:
                self.client.delete_playlist(title)
        except Exception:  # the create error is the one reported
            pass
