# Roadmap

Planned and completed work. Current behavior is in `README.md` and
`docs/DESIGN.md`.

## Milestone 1: Clean foundation

- [x] Full catalog and dual source scopes
- [x] Hierarchical taxonomy and artist profiles
- [x] Deterministic interleaving and album grouping
- [x] Rock, Americana, Jazz, Blues, and collection playlist definitions
- [x] Repository instructions and conventions

## Milestone 2: Validation and audit

- [x] Validate every playlist, tag, artist reference, and ordering strategy
- [x] Add `music-playlists report` with overlap, domination, and coverage
- [x] Diagnose zero and low Rockbox coverage with per-playlist explanations
- [x] Reject unknown definition keys and colliding playlist filenames
- [x] Suppress empty playlist files and prune stale playlists and manifests

## Milestone 3: Rich knowledge model

- [x] Artist identities, aliases, and punctuation-insensitive matching
- [x] Explicit related identities with opt-in inclusion
- [x] Catalog parsed directly from the library export
- [x] Album profiles with pattern rules and edition-tolerant matching
- [x] Album-level tag and flag overrides (`clear_tags`, `clear_flags`)
- [x] Disc numbers in album order, manifests, and Plex resolution
- [ ] Distinct taxonomy dimensions for era and activity. Style, scene, label,
  mood, instrumentation, and ensemble are already modeled.

## Milestone 4: Version selection

- [x] Studio, compilation, remaster, deluxe, box-set classification
- [x] Duplicate collapsing with a retained live version
- [x] Live quota and studio preference in genre playlists
- [x] Same-song spacing within a sequenced playlist
- [ ] Preferred-master selection per album

## Milestone 5: Sequencing

- [x] Artist and album gap constraints
- [x] Cornerstone weighting during candidate balancing
- [x] Song prominence weighting, measured then inferred
- [x] Device capacity limits with representative trimming
- [ ] Album anchors and energy curves
- [ ] Complete-album and mini-album blocks for playlists that request them
  (`complete_album`, Musical Constitution #8)
- [ ] Stable insertion when the catalog changes

## Milestone 6: Collections and outputs

- [x] Canonical playlist manifests
- [x] Plex synchronization: status, index, popularity, resolve, diff, sync
- [x] Song popularity knowledge exported from Plex metadata
- [x] Optional Last.fm enrichment, opt-in and off by default
- [ ] True album chronology and preferred editions. `chronological_album` is
  accepted but sorts by album name until release years exist.
- [ ] Run a full Plex index and apply a first sync against the live server

## Milestone 7: Beyond Plex

- [ ] Additional destinations from the same manifests (car, vacation)
- [ ] Track-level profiles
- [ ] Release-year metadata to support era playlists
- [ ] Richer statistics reporting

## Open taxonomy questions

- [ ] Remove the `Pat Metheny Group` profile or re-tag the catalog
  (`docs/CLASSIFICATION_NOTES.md`)
