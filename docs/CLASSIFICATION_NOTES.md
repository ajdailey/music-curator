# Classification notes

Rationale for classification decisions, and the open questions that need a
human answer. A disputed decision is recorded here, not in a code comment.

Catalog figures below were measured against the library export dated
2026-07-19.

## Settled decisions

- Grunge is broad and includes STP, Candlebox, Bush, and Silverchair.
- Seattle Sound uses the narrower `seattle-scene` tag.
- 90s Rock is broader than Grunge and 90s Alternative.
- ECM is modeled as a label-associated family. Label affiliation is separate
  from musical style; an artist or album may carry both.
- Bruce Springsteen and John Mellencamp are cornerstone Heartland Rock artists.
  `importance: cornerstone` plus the artist soft caps let their catalogs anchor
  the playlist without appearing consecutively or dominating long stretches.
- Tom Petty is Heartland Rock, folk rock, Americana-adjacent and driving
  music, and is a cornerstone artist rather than generic classic rock.
- Hard Bop is a genre playlist sequenced in non-album order, rotating artists
  and albums like a programmed radio format.
- Live recordings are retained (Musical Constitution #3).
- `blues` sits directly under `music` and does not feed Americana. The Blues
  playlist matches `blues` and `blues-rock`, so John Lee Hooker plays beside
  the blues-rock artists; `blues-rock` also stays a child of `rock`.
  `rock-and-roll` (Elvis Presley) and `metal` (Metallica, `Motörhead`) are
  children of `rock`, reach Rock Library, and have no playlist of their own.

## Big Band and Swing

Big Band is an ensemble format; Swing is a style and an era. `big-band` is a
child of `jazz`, and the two are tagged independently:

| Artist | Tags |
| --- | --- |
| Duke Ellington | big-band, swing |
| Count Basie | big-band, swing |
| Glenn Miller | big-band, swing (not present in the catalog) |
| Stan Kenton | big-band only, as progressive post-swing big band |

Do not declare `big-band` a child of `swing`. That containment forces every
big-band artist into the Swing playlist and makes the two playlists
byte-identical.

The two playlists overlap at Jaccard 0.330 on Plex, with 56 percent of Swing
contained in Big Band, and some overlap is correct. Lester Young is the only
small-group swing artist profiled; the rest of the Swing playlist is the two
big-band orchestras.

## Containment overlaps

Both pairs below were reviewed and accepted: no exclusions are applied and no
era dimension was introduced. `report` keeps measuring the overlap, so a
worsening figure stays visible.

### College Rock and 80s Alternative

Jaccard 0.637, with 89 percent of the smaller playlist contained in the larger.
80s Alternative matches `college-rock`, `jangle-pop` and `new-wave`, so College
Rock is a subset by construction.

Options considered:

1. Accept it as a narrower cut of 80s Alternative.
2. Make 80s Alternative an era playlist that excludes `college-rock`, leaving
   College Rock as the scene playlist.
3. Introduce an era dimension so "80s" is a time filter rather than a genre
   union.

Decision: accept. College Rock is a deliberate narrower cut, and R.E.M.
belongs in both playlists.

### Singer-Songwriter and Singer-Songwriter Rock

Jaccard 0.439, with 84 percent containment. `singer-songwriter-rock` declares
`singer-songwriter` as a parent, so the rock playlist is a subset by
construction.

Decision: accept, for the same reason.

## Open: Pat Metheny Group

The catalog bills everything as `Pat Metheny`. `Pat Metheny Group` is profiled
separately: it carries `jazz-fusion` where `Pat Metheny` carries `ecm-label`,
and both carry `contemporary-jazz` and `guitar-jazz`. An alias would silently
merge two deliberate classifications, so none is declared. Open question:
remove the Group profile, or re-tag the catalog.

## Non-artist folders

`excluded_artists` declares `Compilations` (9,418 tracks, 320 various-artists
albums) and `Pickin' On Series` (1,803 tracks of bluegrass tribute covers).
Neither is an artist: a compilation's real per-track artist is lost to the
folder name, and the Pickin' On covers are played by session musicians, not by
the artists being covered.

## Album-level classification

`profiles/albums.yaml` moves Springsteen's acoustic and folk records out of the
rock playlists: `Nebraska`, `The Ghost of Tom Joad` (and their Album Collection
copies), `Nebraska Outtakes`, `Devils & Dust` and the Seeger Sessions add
`folk` and clear `heartland-rock`, `singer-songwriter-rock` and `rock`.
`Nebraska '82_ Expanded Edition` keeps the artist tags, because it includes
the E Street Band "Electric Nebraska" sessions. `Harvest` adds `folk-rock`.

Studio albums whose titles trip version detection carry `clear_flags`: Hole
`Live Through This`, Pat Metheny `We Live Here` and Korn `Korn III` ("Are You
Ready To Live?") clear live; the full-album remixes of Rush `Vapor Trails`,
Pink Floyd `A Momentary Lapse of Reason` and The Who `Tommy` and `It's Hard`
clear remix. `Delicate Sound of Thunder (2019 Remix)`, Buddy Rich `Big Swing
Face` and Thad Jones `Live At The Village Vanguard` clear remix and stay live:
their titles read `(Live_Remix)` or `(Live_Remixed '94)`, a reissue remix.
Albert King `Live Wire_Blues Power` adds `live`, because the `Live Wire` song
exclusion also matches the album name. Neil Young `Acoustic Electric` adds
`live` for the three titles that carry no live marker.

The version patterns treat `_` as a word separator, because filenames store a
colon as `_` (`Live_ London 2013`). The newly matched album and title names
were checked against the export; the Korn and remix entries above are the
false positives found. The Enja Heritage Collection is a reissue series and is
excluded from the compilation release context.

| Rule | Albums | Tracks matched | Tracks reflagged |
| --- | --- | --- | --- |
| `dated-concert-recording` | 277 | 7,732 | 2,242 |
| `live-venue-recording` | 1,813 | 25,720 | 1,356 |
| Explicit entries with flags | 14 | 288 | 267 |

A track counts as reflagged when the rule changes its flags. Most
dated-concert tracks already carry `live` from their titles. No track is
reflagged by two rules, so the total is 3,865. Pearl Jam (5,320) and
Springsteen (2,113) supply most dated-concert tracks. `live-venue-recording`
also matches `concert`, because every `concert` album in the export is a
concert recording (`The Koln Concert`).

### Era overrides

Miles Davis carries `cool-jazz`, `hard-bop`, `modal-jazz` and `jazz-fusion`
across the catalog. Album entries place his records by era:

| Era | Albums | Tags |
| --- | --- | --- |
| Birth of the Cool sessions | 2 | adds `cool-jazz`; clears `hard-bop`, `modal-jazz`, `jazz-fusion` |
| `Kind Of Blue` | 2 editions | clears `jazz-fusion`, `hard-bop` |
| Second quintet studio albums | 4 | adds `post-bop`; clears `cool-jazz`, `jazz-fusion` |
| Other acoustic records, before 1968 | 59 | clears `jazz-fusion` |
| Electric period, 1969-1975 and 1981-1991 | 41 | clears `cool-jazz`, `hard-bop`, `modal-jazz` |

Transitional records (`Miles In The Sky`, `Filles De Kilimanjaro`, `Water
Babies`, `Directions`, `Circle In The Round`) and multi-era compilations keep
the artist tags: 24 albums. Miles Davis holds 25.0 percent of Jazz Fusion and
27.5 percent of Modal Jazz on Plex.

John Mellencamp's records from `Trouble No More` (2003) onward add `americana`
and clear `heartland-rock`: 11 albums, including the three live records of the
period. They move from Heartland Rock to Americana and stay in Rock Library and
Singer-Songwriter Rock, since they are roots rock rather than acoustic folk.

## Dominated playlists

`report` lists 43 playlist-targets where one artist holds 25 percent or more
after balancing. Those at 40 percent or more:

- Rockbox playlists with one to four synced artists: Outlaw Country, Piano
  Trio, Big Band, Cool Jazz, Piano Jazz, Bebop, Modal Jazz, ECM, Folk, Folk
  Rock and Jazz Essentials. These are catalog limits.
- Heartland Rock: Springsteen holds 40.6 percent on Plex and 40.4 percent on
  Rockbox; see `## Open questions`.

Folk Rock was a taxonomy defect: Tom Petty was its only artist. Bob Dylan, The
Byrds, Buffalo Springfield, Crosby, Stills & Nash, Simon & Garfunkel, Donovan,
Mumford & Sons, The Lumineers and Van Morrison carry `folk-rock`; Petty holds
15.9 percent on Plex and 46.6 percent on Rockbox.

## Identity corrections

The report's "profiled artists absent from catalog" check found profiles whose
spelling did not match the catalog billing. Each is aliased in
`profiles/identities.yaml`:

| Profile | Catalog billing |
| --- | --- |
| R.E.M. | R.E.M_ (Windows forbids a trailing dot) |
| Art Blakey & The Jazz Messengers | Art Blakey |
| Carlos Santana | Santana |
| Dr. Lonnie Smith | Lonnie Smith |
| Stevie Ray Vaughan | Stevie Ray Vaughan & Double Trouble |

Deliberately not aliased: Jimmie Vaughan and The Vaughan Brothers are separate
artists, Buffalo Springfield and CSNY are not Neil Young, and solo Beatles
catalogs are not Beatles records.

## Profiled artists absent from the catalog

Ahmad Jamal, Allan Holdsworth, Camel, Eberhard Weber, Gentle Giant, Gillian
Welch, Glenn Miller, Jan Garbarek, Jean-Luc Ponty, Joan Baez, Kris
Kristofferson, Lucinda Williams, Pat Metheny Group, Robin Trower, Slobberbone,
Terje Rypdal.

None appears in the export under another billing. These profiles match nothing
and affect no playlist. Keep one the library may grow into; remove the rest.

## Unclassified artists

1,607 of 1,862 catalog artists carry no profile, since the taxonomy covers
curated artists only. The largest are `Compilations` (9,418 tracks, a
various-artists folder), `Pickin' On Series`, Prince, Benny Carter, Rod
Stewart, The Doors, Fleetwood Mac, Saxon and Whitesnake.

An unclassified artist appears in no genre playlist. `report` ranks them by
track count, so the highest-value additions are visible.

## Open questions

- Springsteen holds 40.6 percent of Heartland Rock on Plex once Mellencamp's
  Americana records leave it. A soft-cap change or more heartland artists
  would lower it; neither is decided.
- Prince (1,089 tracks) is unprofiled: no declared tag fits funk, R&B or
  Minneapolis-sound pop.
- Springsteen's `Western Stars` is orchestral country-pop and carries
  `heartland-rock`. No declared tag fits it.
- Left unprofiled because the genre is disputed: Dream Theater (progressive
  metal; 622 Rockbox tracks would feed Progressive Rock), Peter Gabriel,
  Supertramp, Bruce Hornsby, Bryan Adams, The Hold Steady, and Frank Sinatra,
  Tony Bennett, Nat King Cole, Norah Jones and Louis Armstrong for Vocal Jazz.
