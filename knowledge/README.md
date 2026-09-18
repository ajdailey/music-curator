# knowledge

Generated knowledge, as opposed to the curated knowledge in `profiles/` and
`taxonomy/`. Nothing here is hand-edited.

## song-popularity.json

Global listener counts per song. `music-playlists plex popularity` exports
them from the Plex library index, and `music-playlists lastfm enrich` fills
songs Plex has no count for. Generation reads this file to rank an artist's
signature songs above deep cuts when a playlist is trimmed to fit a device.

The file is gitignored: its size scales with the catalog, and an index plus an
export rebuilds it.

Rebuild it with:

```bash
./scripts/plex.sh index --refresh   # or --resume after an interruption
./scripts/plex.sh popularity
./scripts/generate.sh               # playlists now use it
```

Generation works without the file. Prominence then falls back to the
re-release heuristic in `src/music_engine/scoring.py`, capped so an inferred
score never outranks a measured one. A corrupt or other-schema file prints a
warning and is treated as absent. `report` states how many songs carry a
measured count.
