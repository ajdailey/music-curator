# Vision and design intent

Music Curator is a personal music knowledge base that generates playlists. It
models artists, albums, genres, musical relationships, recording versions and
listening behavior, and generates Plex playlists, Rockbox playlists, reports
and statistics from that knowledge, deterministically.

`DESIGN.md` describes how the system works; this document states what it is
for.

## Goals

- Maintain a single music knowledge base.
- Generate curated playlists for multiple destinations.
- Preserve deterministic behavior.
- Preserve musical intent.
- Minimize manual maintenance.
- Make classification decisions explicit and auditable.
- Let the taxonomy evolve without rewriting generation logic.

## Classification approach

The taxonomy records how an experienced collector classifies a library, not
the output of loose automatic heuristics:

- Bruce Springsteen belongs in Heartland Rock, and also in Americana.
- Certain albums belong in Singer-Songwriter Rock.
- `Nebraska` has a different musical identity than `Born in the U.S.A.`
- Pearl Jam belongs in Grunge, Seattle Sound, and 90s Rock.
- Stone Temple Pilots belong in Grunge and 90s Rock but not Seattle Sound.
- Radiohead is not Britpop merely because the band is British.

These decisions live in the taxonomy, never in playlist code.

## Knowledge first

The repository consists primarily of structured musical knowledge: artist
profiles, album metadata, genre and style taxonomy, moods, eras,
instrumentation, record labels, playlist definitions, sequencing preferences,
artist relationships, aliases, and collection definitions. The code consumes
this knowledge and does not encode it. As the collection grows, the repository
evolves by refining that knowledge, not by rewriting code.

## Separation of responsibilities

### Catalog

The complete collection: track-level metadata only, no playlist logic. It is
parsed directly from the library export, so it cannot drift from the library.

### Taxonomy

Musical relationships: genres, styles, scenes, instrumentation, ensemble
types, labels and moods. These stay distinct concepts, declared as tags in one
parent hierarchy (`taxonomy/tags.yaml`). Era and activity are not yet modeled
(`ROADMAP.md`).

### Artist profiles

Tags, importance (cornerstone status), and the songs that are essential or
demoted. Aliases, related identities and exclusions are declared in the
identity profiles.

### Album profiles

The tags and flags an album adds to or clears from its artist's, masterpiece
status, complete-album preference, and release year, given as a per-album entry
or a pattern rule matching a family of releases. An album can belong somewhere
its artist does not.

### Playlist definitions

The intent of a playlist: which taxonomy it uses, its sequencing strategy, its
balancing strategy. A definition does not duplicate taxonomy and does not list
hundreds of artists.

### Generation engine

Combines the catalog, taxonomy, profiles and playlist definitions into output
playlists.

## Output targets

Plex uses the entire collection. Rockbox uses only tracks in `iPod Sync.m3u8`,
the curated portable library, and its output is always the playlist
intersected with that library. No playlist keeps a separate portable
definition. Future targets (car, vacation, streaming exports, statistics) come
from the same knowledge.

## Playlist identity

Grunge, Seattle Sound, 90s Rock, College Rock, Americana, Roots Rock and Alt
Country are each a distinct listening experience. Musical coherence decides
whether a playlist is right, not its track count. Two playlists with nearly
identical membership indicate a taxonomy problem.

## Collections and genre playlists

Collections preserve albums: chronological ordering, album grouping, album
track order, and deliberate placement of live albums, archival releases,
deluxe editions, remasters and compilations. Artist collections keep the
complete discography and are never balanced.

Genre playlists preserve flow: artist spacing, album spacing, live spacing,
duplicate spacing, cornerstone weighting, and deterministic stable ordering.
Alphabetical sorting is not acceptable sequencing.

Selecting what belongs in a playlist and ordering it are separate problems.
Selection runs first, so a large artist catalog cannot dominate a genre
playlist merely by containing thousands of recordings.

## Recording versions

The taxonomy covers studio recordings, live recordings, demos, alternate takes,
radio edits, box sets, deluxe editions, remasters, greatest hits, and
compilations. Genre playlists favor primary studio recordings, keep important
live performances, and avoid five versions of one song. Artist collections
preserve historical completeness.

## Artist identity

Artists have canonical identities with explicit aliases and explicitly
declared related identities. Punctuation variants resolve automatically. Side
projects are never merged by guesswork, and no fuzzy matching is applied.

## Validation

`validate`, `pytest`, `generate` and `report` surface missing artists, unused
taxonomy, zero-count playlists, playlist overlap, duplicate recordings, artist
domination, unloaded playlists, Rockbox coverage, collection issues, path
normalization problems, alias problems, and taxonomy inconsistencies. Each
report section states why a result occurred, not only what it was.

## Questions the knowledge base answers

- Which artists define Heartland Rock?
- Which albums are masterpieces?
- Which playlists overlap too much?
- Which artists dominate a playlist?
- Which recording should be preferred when five versions exist?
- Which tracks belong on a portable device?
- Which albums should always be played in sequence?
