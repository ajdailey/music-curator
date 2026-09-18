# Design

How the system works and why. `VISION.md` states the design intent.

---

## 1. Scope

Music Curator generates playlists for several destinations from a library
export plus curated knowledge.

```
Library export   |
Taxonomy         |
Artist profiles  +--> Generation engine --> Playlists --> Plex (destination)
Album profiles   |                          Manifests
Identities       |                          Reports
```

Knowledge lives in YAML: adding a playlist means editing taxonomy or profiles,
never Python. Identical inputs produce identical outputs; Sec 9 lists how.

---

## 2. Invariants

Each invariant holds by construction and is covered by tests. Breaking one is a
defect.

| Invariant | Enforced by |
| --- | --- |
| Rockbox output is a subset of Plex output for the same playlist | Canonical playlist built once, then intersected (`pipeline.generate_playlists`) |
| Regeneration is byte-identical given identical inputs | Seeded hashes, sorted iteration, no `random` or `time` in selection |
| Artist collections are never trimmed | `balancing_by_type.collection.enabled: false` |
| An empty playlist is never written, and a stale playlist or manifest is removed | `core.write_m3u`, `pipeline.prune_stale_outputs` |
| Only `MC - ` playlists are ever modified in Plex | `synchronizer.is_managed`, checked again in `_write` |
| Nothing is written to Plex without `--apply` | `PlexSynchronizer.sync(apply=False)` default |
| A populated playlist is never replaced by an empty one | `SyncThresholds.evaluate`, `client.create_playlist` |
| Credentials come only from the environment | `PlexSettings.from_env`, `LastfmSettings.from_env` |

---

## 3. Data model

### 3.1 Track

`core.Track` is frozen. Every field is derived from the export, then album
knowledge (Sec 4.3) adjusts `flags` and sets `cleared_flags`.

```python
Track(artist, album, title, source_path, relative_path, flags, track_no,
      disc_no, cleared_flags)
```

`Track.key` is the normalized relative path: forward slashes, case-folded. It
is the identity for set operations, portable intersection, and Plex resolution.

### 3.2 The 40-character truncation

The export truncates every path segment at 40 characters. Measured against the
library export: 3,487 album names sit exactly at the limit, often with an
unclosed parenthesis (`Automatic For The People (25th Anniversa`). Artists and
titles are truncated the same way. As a result:

- album names cannot be compared exactly, so `albums.base_album` normalizes
  them;
- long titles can collide, so song grouping is always scoped to one artist;
- Plex holds untruncated metadata, so Plex resolution prefers path matching
  over metadata matching.

---

## 4. Pipeline

The stages run in a fixed order.

```
1. Catalog loading      What exists?
2. Identity resolution  Who is this artist, really?
3. Album knowledge      What do we know about these records?
4. Source scoping       What is available on this destination?
5. Taxonomy matching    What belongs in this playlist?
6. Candidate balancing  How much of it, and which recordings?
7. Capacity             Does it fit the device?
8. Sequencing           In what order does it play?
9. Output               Write playlists and manifests
10. Reporting           Is any of this suspicious?
```

### 4.1 Catalog loading (`catalog.py`)

`catalog.py` parses `Library Playlist.m3u8` directly. There is no snapshot;
refreshing the catalog means replacing the file.

Fields come from the path:

- `artist`, `album` - the first two path segments
- `disc_no`, `track_no`, `title` - the filename (`1-01 Humph` -> disc 1,
  track 1, title `Humph`). A filename without a disc prefix leaves `disc_no`
  unset.
- `flags` - derived from album and title text (`catalog.derive_flags`)

`catalog.py` does not parse the `#EXTINF` display line. That line is
`Artist - Title`, which is ambiguous whenever a title contains " - "
(`Song - Part Two`).

### 4.2 Identity resolution (`identity.py`)

`IdentityResolver.resolve` tries an exact case-folded match on a canonical name
or declared alias, then a punctuation-insensitive match. A name matching
neither resolves to itself.

`punctuation_key` drops punctuation and spacing together, so `R.E.M.`,
`R.E.M_` and `REM` all collapse to `rem`; keeping spaces would yield `r e m`
and the alias would never match. `&` becomes `and`, and diacritics are folded.
Leading articles are kept, since dropping `The` would merge `The Band` and
`Band`.

`validate` reports two declared identities that share a punctuation key as an
error rather than merging them.

#### Related identities

Related identities are never merged. `Crazy Horse` stays its own artist, and a
playlist opts in with `include_related: true`. Buffalo Springfield and CSNY are
deliberately not Neil Young.

### 4.3 Album knowledge (`albums.py`)

`profiles/albums.yaml` supplies knowledge that artist-level tags cannot
express: `Nebraska` is a folk record by a heartland rock artist, and a dated
concert album is not a studio record.

- **Pattern rules** match many albums by regex. `dated-concert-recording`
  matches the Pearl Jam and Springsteen archival series (`1975_10_18 West
  Hollywood, CA`), where neither the album name nor any track title contains
  "live". Without the rule these concerts are classified as studio takes and
  compete with studio material.
- **Explicit entries** carry per-album `tags`, `flags`, `clear_tags`,
  `clear_flags`, `masterpiece`, `complete_album` and `year`. The last three are
  loaded, but no stage reads them yet.

Pattern and entry `tags` expand to their ancestors, so an album tagged
`folk-rock` matches a playlist on `rock`. `clear_tags` does not expand:
clearing `heartland-rock` leaves `rock` in place. During matching an album's
effective tags are the artist's tags minus `clear_tags`, plus the album's own,
so the matcher caches per `(identity, album)` rather than per artist.

`AlbumProfiles.apply_flags` merges album flags into the catalog at load time,
so version classification and balancing both see them. It removes each
`clear_flags` kind and records it on `Track.cleared_flags`, which also
suppresses text detection of that kind (Sec 4.6). `validate` and
`Project.load` reject an undeclared tag in `clear_tags` and an unknown kind in
`clear_flags`.

Measured against the library export: `dated-concert-recording` matches 277
albums and 7,732 tracks and changes the flags of 2,242; album knowledge as a
whole changes the flags of 3,865 tracks. Per-rule figures and the entries
behind them are in `CLASSIFICATION_NOTES.md`.

#### `base_album` normalization

`base_album` strips edition wrappers and the truncation artifact, so one
declared entry covers every edition:

```
Murmur - Deluxe Edition                  -> murmur
Kind Of Blue (Legacy Edition)            -> kind of blue
Automatic For The People (25th Anniversa -> automatic for the people
```

It strips wrappers, never words: `Harvest` and `Harvest Moon` stay distinct,
and `Nebraska Outtakes` does not collapse into `Nebraska`. An entry may declare
`album_matches` to capture a family explicitly.

### 4.4 Source scoping (`pipeline.Project`)

- `plex_full` - every entry in the export
- `rockbox_ipod` - entries whose `Track.key` occurs in `iPod Sync.m3u8`

Every playlist is built once against `canonical_source` (default `plex_full`).
Each other target takes the balanced canonical playlist, intersects it with its
own library, and re-balances. Re-balancing only removes tracks, so the subset
property holds by construction. Balancing each target independently does not
guarantee it: against an earlier library export, that design produced Britpop
with 510 Plex tracks and 2,007 Rockbox tracks.

### 4.5 Taxonomy matching (`core.PlaylistMatcher`)

`PlaylistMatcher` decides membership per `(identity, album)` and caches the
result, because every criterion except `exclude_flags` is artist-level or
album-level. Generation stays linear in catalog size instead of evaluating
every track against every definition.

Criteria: `match_any_tags`, `match_all_tags`, `include_artists`,
`exclude_artists`, `include_related`, `exclude_flags`.

Tags form a DAG; loading rejects a cycle. A tag's closure includes its
ancestors, so an artist tagged `classic-rock` matches a playlist matching
`rock`. A tag name in a definition or a profile resolves through the `aliases`
in `taxonomy/tags.yaml` before matching.

`definitions.py` rejects an unknown key in a definition, an `ordering` key the
strategy does not read (`sequencing.ORDERING_KEYS`), an unknown `balancing`
key, and two playlist names that write the same output file. `validate`
reports these and `Project.load` raises on them: a misspelled `match_any_tag`
would leave the playlist with no tag criterion and select the whole catalog.

`excluded_artists` in `profiles/identities.yaml` removes non-artist folders
from every playlist: `Compilations` (9,418 tracks of various-artists albums
whose real per-track artist is lost, measured against the library export) and
`Pickin' On Series`. A playlist that names one in `include_artists` still gets
it.

### 4.6 Version classification (`versions.py`)

Classification has two independent axes:

- **performance**: studio, radio-edit, live, alternate, demo, remix
- **release**: original, remaster, expanded, deluxe, box-set, compilation

Each performance kind matches on its catalog flag or on album and title text,
tested in the order remix, live, demo, alternate, radio edit; the first match
wins. Remix is tested first because a remix album is often also named for a
venue or a date. Live detection excludes `Live and Let Die` and `Live Wire`,
which are studio songs. A kind in `Track.cleared_flags` matches neither on its
flag nor on text.

Every version pattern treats `_` as a word boundary (`versions.marker`),
because filenames store a colon as `_` (`Live_ London 2013`) and `\b` finds no
boundary there.

#### Remix demotion

Remix sorts last on the performance axis: a remix is a different arrangement,
so it never stands in for the original recording.

- `REMIX_TEXT` is narrow and shared with `catalog.derive_flags`, so the `remix`
  flag and the `remix` performance kind cannot disagree. It matches remix
  spellings and dance-floor mixes (club, dance, dub, house, techno, electro,
  disco, extended, party, 12-inch). A bare "Mix" is not a remix: `Original
  Mix`, `Mono Mix` and `Final Mix` are the record as released.
- `preference_key` keeps remix demoted even when `prefer_studio` is false. That
  flag chooses between studio and live; turning it off does not let a remix
  replace the original.

`base_title` strips version markers, remix markers included, so recordings of
one song group together; a remix that grouped separately could never lose to
the original. A marker naming a reprise or an instrumental is never stripped,
so `Song (Reprise)` and `Song (Instrumental Version)` are distinct songs.
`select_versions` keeps the best version per song plus a limited number of
live takes (Musical Constitution #3). Remixes get no such allowance.

### 4.7 Candidate balancing (`balancing.py`)

Balancing decides what is in the playlist; sequencing only reorders. Balancing
stops one artist's archive from filling a genre playlist: Pearl Jam holds 5,320
dated-concert tracks, measured against the library export.

`balance_candidates` collapses duplicate recordings, ranks by preference,
applies per-artist soft caps, then enforces the composition quotas.
Cornerstone artists (`importance: cornerstone`) get a multiplier on their cap.

Settings merge in three layers, later winning: `balancing_defaults`, the
`balancing_by_type` entry for the playlist's `type`, then the playlist's own
definition (`Project.balance_config`).

#### Share cap measurement

`solve_share_cap` measures the per-artist share against the candidate pool,
not the finished playlist. The finished playlist can sit slightly above the
share; `report` states the actual figure for every playlist. A cap that lowers
no artist's share of the result is not applied: a single-artist playlist, or
fewer than `1 / share` artists of equal size, keeps every track.

Constraining the finished playlist is self-referential, because its size
depends on the cap. Solving it exactly drags every artist down to the smallest
one: when Outlaw Country had four artists and the smallest held 42 tracks, the
exact solution was a 168-track playlist, discarding 95 percent of the material.
Iterating `cap = share * size` from above collapsed Bebop from 4,502 tracks
to 10. Both figures are historical, measured against an earlier library
export.

#### Composition quotas

`quota_allowance` solves `limited <= share * (others + limited)` for `limited`,
for both `max_live_share` and `max_remix_share`. Using `share * current_size`
instead leaves the result above quota once the dropped tracks are gone. Both
quotas run over a preference-ordered list, so the survivors are the best of
their kind. Trimming one kind shrinks the playlist and can push the other over
its share, so both quotas are reapplied until neither removes a track.

Version collapsing has already removed every remix that had an original to
lose to, so the remix quota sees only songs the catalog holds solely as
remixes. `max_remix_share: 0.0` drops them all at this stage;
`exclude_flags: [remix]` drops every remix earlier, during taxonomy matching.

### 4.8 Capacity (`pipeline.apply_capacity`)

A target may declare `max_tracks`. Rockbox refuses a longer playlist with
"playlist buffer full".

`apply_capacity` rotates artists and albums first, then takes the prefix, so
the retained slice spans every artist and keeps each one's best-known songs.
Truncating the sequenced order would keep an alphabetical or album-order prefix
and lose whole artists.

`max_tracks` is 32,000, the device limit. Nothing is trimmed at that size: the
largest Rockbox playlist, Rock Library, holds 28,606 tracks, measured against
the library export. The cap stays declared so a growing library cannot produce
an unplayable file.

### 4.9 Sequencing (`sequencing.py`)

| Strategy | Used by | Behavior |
| --- | --- | --- |
| `album_order`, `chronological_album`, `album_grouped` | collections | artist, album, disc, track, title |
| `interleaved`, `curated_sequence` | genre playlists | rotate artists and albums |
| `deterministic`, `stable_shuffle` | - | seeded stable order |

`chronological_album` sorts by album name like the other two; the catalog
carries no release year to order by. A track without a disc number sorts as
disc 1, and one without a track number after the numbered tracks of its disc.
Album strategies read no ordering key except `strategy`.

`max_consecutive_artist` and `max_consecutive_album` (interleaved strategies,
default 1) are maximum runs: 1 means no artist or album plays twice in a row.
An artist left as the only one with tracks plays out regardless.

Every non-album strategy then applies same-song spacing (`min_same_song_gap`,
default 25). A playlist can hold a studio and a live take of one song, and a
cover shares its title with the original; spacing keeps them apart. Album
strategies are never spaced, since album order is the record's own sequence.

`space_duplicates` walks the sequence, defers a track whose song appeared
within the gap, and places the earliest eligible deferred track instead. When
nothing is eligible it places the oldest deferred track anyway, which
guarantees termination.

### 4.10 Output

Playlists are written as M3U8 with the target's root prefix. For each
non-empty playlist of the canonical target, generation also writes a manifest
to `output/manifests/`: the exact membership and order, independent of any
destination, with each entry's path, artist, album, title, track and disc
number. Synchronization reads the manifest, never engine internals.

An empty playlist is not written. `prune_stale_outputs` removes every M3U and
manifest the current taxonomy does not produce, so a renamed, deleted or
emptied playlist leaves no file behind and `plex sync --apply` cannot recreate
it from a stale manifest.

---

## 5. Song prominence (`scoring.py`, `popularity.py`)

Prominence ranks an artist's songs so trimming keeps the signature ones.
Measured data is used first, inferred data where none exists.

### 5.1 Measured prominence

Plex's music metadata provider supplies a global listener count
(`ratingCount`) on roughly 85-95 percent of tracks, measured against the live
server: Plush 856k, Interstate Love Song 704k, Creep 590k. `plex popularity`
exports it to `knowledge/song-popularity.json`. The count is attached
inconsistently across editions, so the export takes the maximum across all
copies of a song.

Generation reads the knowledge file, never Plex, so it stays deterministic and
offline and Plex stays a destination. An unreadable file, or one with another
`schema_version`, prints a warning on stderr and is not used. `report` states
how many scored songs carry a measured figure.

### 5.2 Inferred prominence

Without a measured figure, prominence falls back to re-release count: the
number of distinct albums a song appears on, with compilation appearances
weighted double. Inferred scores are capped at 0.5 (`UNMEASURED_CEILING`), so a
guess never outranks measured data.

### 5.3 Applying prominence

Scores are normalized within each artist, so a prolific artist's deep cut
never outranks a smaller artist's biggest song. The `essential` and `demote`
lists in `profiles/artists.yaml` override both sources.

Prominence ranks after recording quality, so a signature song's bootleg never
outranks a studio take.

Measured data changes rankings substantially: Interstate Love Song ranks 18th
of 50 Stone Temple Pilots songs on the re-release heuristic and 2nd on listener
data from the live server.

---

## 6. Knowledge files

| File | Contains |
| --- | --- |
| `taxonomy/tags.yaml` | tag hierarchy and aliases |
| `taxonomy/playlists.yaml` | membership, sequencing, balancing intent |
| `profiles/artists.yaml` | artist tags, importance, essential and demote |
| `profiles/albums.yaml` | album patterns and per-album entries |
| `profiles/identities.yaml` | identities, aliases, related, exclusions |
| `config/settings.yaml` | sources, outputs, balancing policy, report pairs |
| `knowledge/song-popularity.json` | generated listener counts, gitignored |

`rules/portable.yaml` is descriptive only; no code reads it.

Every file above is committed except `knowledge/song-popularity.json`, which is
derived data that scales with the catalog and is rebuilt by one index plus an
export. Without it, generation uses the re-release heuristic.

---

## 7. Plex integration (`integrations/plex/`)

Plex is a destination, never a source. Only `client.py` imports plexapi, so
the engine is testable without a server.

| Module | Responsibility |
| --- | --- |
| `config.py` | environment-only credentials, fail-clean |
| `client.py` | the only plexapi consumer |
| `cache.py` | the library index, checkpointed |
| `resolver.py` | manifest entry to Plex track |
| `synchronizer.py` | diffing and gated writes |

### 7.1 Indexing

Indexing is paged and checkpointed. `client.py` reads the search response XML
directly instead of plexapi attributes: accessing `item.media` on a partial
object triggers a per-track HTTP reload, capping throughput at about 57 tracks
per second. Reading the response gives roughly 600-700 tracks per second end to
end. Both rates were measured against the live server.

An interrupted index, or one holding fewer tracks than the library reports, is
saved incomplete, and every resolving command refuses it, so an unindexed track
is never reported as missing. `plex index --resume` finishes an interrupted
index; a short one needs `--refresh`. The cache records its server and library:
`--resume`, `diff` and `sync` refuse an index from another server, and every
command that reads the index refuses one from another library.

### 7.2 Resolution

`PlexResolver.resolve_entry` tries five tiers in order and takes the first
unique match:

1. normalized path
2. mapped path: the catalog path rewritten onto the Plex media root, then the
   last three path segments as a fallback
3. GUID, only for an entry whose `catalog_id` is a `plex://` GUID; generated
   manifests carry the normalized relative path, so this tier does not fire
   for them
4. artist, album, disc, track and title. A Plex track with no disc number
   counts as disc 1. A manifest entry with no disc number is matched without
   the disc, so it is ambiguous when two discs of the album hold the same
   track number and title.
5. artist and title within the same album

Title alone is never sufficient: many recordings share a title, and matching
on it attaches the wrong one. An entry for which some tier matched several
Plex tracks and no tier matched exactly one is reported ambiguous, never
guessed.

Verified against the live server: 107 of 107 tracks matched by exact path.

### 7.3 Synchronization

Ownership is the `MC - ` prefix. Playlist lookups are scoped to the configured
library, since a server can host several music libraries and
`server.playlists()` spans all of them.

A write requires the managed prefix, an explicit `--apply`, resolution within
the unresolved and ambiguous thresholds (`--max-unresolved` and
`--max-ambiguous`, default 5 and 2 percent), and a non-empty result.

`_write` builds `<title> (syncing)` and verifies its items against the rating
keys. It then renames the live playlist to `<title> (previous)`, renames the
staging copy to `<title>`, and deletes `(previous)`. An interruption at any
step leaves either the verified playlist or the previous one under one of the
two names, and a rerun deletes the leftover copy. A failed create or
verification deletes the staging copy and leaves `<title>` untouched.

A Plex request error on one playlist is recorded in `reports/plex-sync.json`
and the run continues with the next. `sync` exits 1 when any playlist failed,
was refused on thresholds, or failed verification.

#### Batched playlist creation

`client.create_playlist` sends rating keys `PLAYLIST_BATCH_SIZE` (200) at a
time: the first batch creates the playlist and the rest are appended in order.
Plex puts every rating key in the request URL. Measured against the live
server, a 4,496-track playlist in one request produced a request line of about
45 KB, which the server rejected with 400.

Rating keys are posted directly rather than resolved into plexapi objects;
`server.fetchItem` costs one round trip per track, so Rock Library (50,130
tracks, measured against the library export) would issue 50,130 requests.
Verified against the live server: a 6,198-track playlist writes and verifies in
about 35 seconds.

---

## 8. Last.fm (`integrations/lastfm/`)

Last.fm enrichment is opt-in: a separate command with a separate API key,
never invoked by generate, report or sync. It fills only the 5-15 percent of
songs that have no Plex listener count, because at its rate limit a full
backfill runs for hours where the Plex index runs for minutes. The rate is
`MUSIC_CURATOR_LASTFM_RATE_LIMIT`, default and ceiling 5 requests per second.

A hit and a "track not found" answer are cached, so a rerun never repeats
them. A failed request is not cached and a rerun retries it. A rejected API key
stops the run (exit 2), and 10 consecutive failed requests stop it (exit 1);
progress is saved either way.

---

## 9. Determinism

- Ordering ties break on `sha256(seed | artist | album | title | path)`.
- Dict iteration never reaches output; results are sorted before use.
- No `random`, and no wall-clock input to selection.

Output changes only when the export, the knowledge files, or the configuration
changes.

---

## 10. Reporting (`analysis.py`, `reporting.py`)

`music-playlists report` states what generated, what did not, and why.

Sections: zero-count explanations distinguishing genuine absence from a defect,
identities and possible missing aliases, playlist overlap with Jaccard
similarity, artist domination with inverse-Simpson effective diversity,
balancing effects, portable coverage, taxonomy gaps, version statistics, album
knowledge coverage, measured popularity coverage, and capacity trimming.

| Flag | Threshold |
| --- | --- |
| Dominating artist | 25 percent or more of a playlist |
| Effective duplicate playlists | track Jaccard 0.9 or more |
| Live-heavy playlist | 40 percent or more live |
| Remix-heavy playlist | 10 percent or more remix |

A balanced playlist sits near zero on the remix figure. A higher figure means
the quota is misconfigured or those songs exist only as remixes.

Collections are measured but never flagged as dominated: a Springsteen
collection is 100 percent Springsteen by definition.

---

## 11. Extension points

| Extension | How |
| --- | --- |
| New playlist | Add it to `taxonomy/playlists.yaml`. No code. |
| New tag dimension | Add it to `taxonomy/tags.yaml` and reference it. |
| New album knowledge | Add a pattern or an entry to `profiles/albums.yaml`. |
| New destination | Consume `output/manifests/` and add an `integrations/` package. Do not read engine internals. |
| New popularity source | Write `knowledge/song-popularity.json` in the documented shape. Scoring is source-agnostic. |

---

## 12. Known limitations

- **Artist-level classification.** Most taxonomy is per artist. Album entries
  override it for cross-era artists (Sec 4.3); no track-level override
  exists.
- **No year metadata.** The export carries no release year, so era playlists
  need hand-curated years. `profiles/albums.yaml` accepts `year`, which no
  stage reads yet.
- **Truncation.** 40-character names occasionally collide. Grouping is scoped
  per artist to limit the damage.
- **Prominence is a proxy.** Listener count measures fame rather than quality,
  and the re-release fallback measures reissue frequency. The `essential` and
  `demote` lists correct both.
- **Containment overlaps** between College Rock and 80s Alternative, and
  between Singer-Songwriter and Singer-Songwriter Rock, are accepted by
  decision. See `CLASSIFICATION_NOTES.md`.
