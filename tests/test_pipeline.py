"""End-to-end pipeline behavior against a synthetic project."""

from __future__ import annotations

import json

import pytest
import yaml

from music_engine.cli import generate, report, validate
from music_engine.pipeline import Project, generate_playlists

WINDOWS_ROOT = "M:\\Music\\"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def project_root(tmp_path):
    """A miniature project reproducing the catalog's real hazards."""
    rows: list[dict] = []

    def row(artist, album, no, title, flags=""):
        rel = f"{artist}/{album}/{no:02d} {title}.m4a"
        rows.append({
            "artist": artist,
            "album": album,
            "track_no": no,
            "title": title,
            "extension": ".m4a",
            "duration_seconds": 200,
            "display": f"{artist} - {title}",
            "source_path": "M:\\Music\\" + rel.replace("/", "\\"),
            "relative_path": rel,
            "plex_path": f"/plex/{rel}",
            "rockbox_path": f"../Music/{rel}",
            "flags": flags,
        })

    # R.E.M. stored with the filesystem-sanitized name, as in the real export.
    for i in range(1, 7):
        row("R.E.M_", "Murmur", i, f"Song {i}")
    # An artist present in the catalog but absent from the portable library.
    for i in range(1, 5):
        row("Neil Young", "Harvest", i, f"Song {i}")
    # A dominant artist with a large live catalog.
    for i in range(1, 21):
        row("Pearl Jam", "Live Bootleg", i, f"Song {i}", flags="live")
    for i in range(1, 4):
        row("Pearl Jam", "Ten", i, f"Studio {i}")
    for i in range(1, 5):
        row("Soundgarden", "Badmotorfinger", i, f"Song {i}")

    # The library export is the catalog: filenames are the source of truth.
    export = ["#EXTM3U", "#PLAYLIST:Library Playlist"]
    for r in rows:
        export.append(f"#EXTINF:{r['duration_seconds']},{r['display']}")
        export.append(r["source_path"])
    (tmp_path / "Library Playlist.m3u8").write_text(
        "\n".join(export) + "\n", encoding="utf-8"
    )

    # The portable library excludes Neil Young entirely.
    sync = ["#EXTM3U"] + [
        r["source_path"] for r in rows if r["artist"] != "Neil Young"
    ]
    (tmp_path / "iPod Sync.m3u8").write_text("\n".join(sync) + "\n", encoding="utf-8")

    write(tmp_path / "config/settings.yaml", {
        "canonical_source": "plex_full",
        "catalog": {
            "source": "Library Playlist.m3u8",
            "windows_root": WINDOWS_ROOT,
        },
        "sources": {
            "plex_full": {"scope": "full_catalog"},
            "rockbox_ipod": {
                "scope": "playlist_intersection",
                "playlist": "iPod Sync.m3u8",
                "windows_root": WINDOWS_ROOT,
            },
        },
        "outputs": {
            "plex": {"source": "plex_full", "root": "/plex/", "directory": "output/plex"},
            "rockbox": {
                "source": "rockbox_ipod",
                "root": "../Music/",
                "directory": "output/rockbox",
            },
        },
        "balancing_by_type": {
            "genre": {"enabled": True, "max_share_per_artist": 0.34, "max_live_share": 0.25},
            "collection": {"enabled": False},
        },
        "reporting": {"overlap_pairs": [["Grunge", "Rock"]]},
    })
    write(tmp_path / "taxonomy/tags.yaml", {
        "tags": {
            "music": {},
            "rock": {"parents": ["music"]},
            "grunge": {"parents": ["rock"]},
            "college-rock": {"parents": ["rock"]},
        },
        "aliases": {},
    })
    write(tmp_path / "profiles/artists.yaml", {
        "artists": {
            "R.E.M.": {"tags": ["college-rock"], "importance": "cornerstone"},
            "Neil Young": {"tags": ["rock"]},
            "Pearl Jam": {"tags": ["grunge"]},
            "Soundgarden": {"tags": ["grunge"]},
        }
    })
    write(tmp_path / "profiles/identities.yaml", {
        "identities": {"R.E.M.": {"aliases": ["R.E.M_"]}}
    })
    write(tmp_path / "taxonomy/playlists.yaml", {
        "playlists": [
            {"name": "Rock", "type": "genre", "match_any_tags": ["rock"],
             "ordering": {"strategy": "interleaved", "seed": "rock"}},
            {"name": "Grunge", "type": "genre", "match_any_tags": ["grunge"],
             "ordering": {"strategy": "interleaved", "seed": "grunge"}},
            {"name": "R.E.M. Collection", "type": "collection", "include_artists": ["R.E.M."],
             "ordering": {"strategy": "album_grouped"}},
            {"name": "Neil Young Collection", "type": "collection",
             "include_artists": ["Neil Young"],
             "ordering": {"strategy": "album_grouped"}},
        ]
    })
    (tmp_path / "reports").mkdir()
    return tmp_path


def results_for(project_root):
    project = Project.load(project_root)
    return project, generate_playlists(project, write=False)


def by_name(results, name, target):
    return next(r for r in results if r.name == name and r.target == target)


# --- the R.E.M. defect ----------------------------------------------------


def test_collection_finds_the_sanitized_catalog_spelling(project_root):
    _, results = results_for(project_root)
    assert by_name(results, "R.E.M. Collection", "plex").count == 6
    assert by_name(results, "R.E.M. Collection", "rockbox").count == 6


# --- the subset invariant -------------------------------------------------


def test_rockbox_is_always_a_subset_of_plex(project_root):
    """Regression: balancing each target independently broke this.

    Rockbox playlists were coming out LARGER than their Plex counterparts
    because the artist cap was solved against a different candidate pool.
    """
    _, results = results_for(project_root)
    for result in results:
        if result.target != "rockbox":
            continue
        plex = by_name(results, result.name, "plex")
        assert result.count <= plex.count, result.name
        assert {t.key for t in result.tracks} <= {t.key for t in plex.tracks}


def test_portable_playlist_excludes_absent_artists(project_root):
    _, results = results_for(project_root)
    assert by_name(results, "Neil Young Collection", "plex").count == 4
    assert by_name(results, "Neil Young Collection", "rockbox").count == 0


# --- balancing integration ------------------------------------------------


def test_genre_playlist_is_balanced_but_collection_is_not(project_root):
    _, results = results_for(project_root)
    grunge = by_name(results, "Grunge", "plex")
    live = sum(1 for t in grunge.tracks if "live" in t.flags)
    assert live / grunge.count <= 0.25  # live quota enforced
    # The collection keeps every track, untouched.
    assert by_name(results, "R.E.M. Collection", "plex").count == 6


def test_generation_is_deterministic(project_root):
    _, first = results_for(project_root)
    _, second = results_for(project_root)
    # strict: a differing result count is itself a determinism failure, and a
    # plain zip would compare the shorter prefix and pass.
    for a, b in zip(first, second, strict=True):
        assert [t.relative_path for t in a.tracks] == [t.relative_path for t in b.tracks]


# --- output behavior ------------------------------------------------------


def test_empty_playlists_are_not_written(project_root):
    generate(project_root)
    assert not (project_root / "output/rockbox/Neil Young Collection.m3u8").exists()
    assert (project_root / "output/plex/Neil Young Collection.m3u8").exists()

    summary = json.loads((project_root / "reports/generation-summary.json").read_text())
    assert "Neil Young Collection" in summary["empty_playlists_not_written"]


def test_stale_output_files_are_removed(project_root):
    stale = project_root / "output/plex/Deleted Playlist.m3u8"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("#EXTM3U\n", encoding="utf-8")
    generate(project_root)
    assert not stale.exists()


# --- commands -------------------------------------------------------------


def test_validate_passes_on_a_consistent_project(project_root):
    assert validate(project_root) == 0


def test_validate_fails_on_an_undeclared_tag(project_root):
    write(project_root / "taxonomy/playlists.yaml", {
        "playlists": [{"name": "Bad", "match_any_tags": ["nonexistent-tag"]}]
    })
    assert validate(project_root) == 1


def test_report_explains_zero_counts_as_genuine_absence(project_root):
    assert report(project_root) == 0
    data = json.loads((project_root / "reports/audit-report.json").read_text())
    zero = {(z["playlist"], z["target"]): z for z in data["zero_counts"]}
    entry = zero[("Neil Young Collection", "rockbox")]
    assert entry["reason"] == "absent_from_source"
    assert entry["plex_candidates"] == 4


def test_report_records_aliases_and_domination(project_root):
    report(project_root)
    data = json.loads((project_root / "reports/audit-report.json").read_text())
    identities = {d["identity"]: d for d in data["identities"]["declared_identities"]}
    assert identities["R.E.M."]["catalog_spellings"] == ["R.E.M_"]
    assert identities["R.E.M."]["catalog_tracks"] == 6
    assert data["artist_domination"]["playlists"]
    assert data["rockbox_coverage"]["sources"]["plex_full"] == 37


# --- device capacity ------------------------------------------------------


def test_capacity_limit_trims_oversized_playlists(project_root):
    """Rockbox reports 'playlist buffer full' beyond its configured maximum."""
    settings = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    settings["outputs"]["rockbox"]["max_tracks"] = 5
    write(project_root / "config/settings.yaml", settings)

    _, results = results_for(project_root)
    for result in results:
        if result.target == "rockbox":
            assert result.count <= 5, result.name


def test_capacity_limit_does_not_affect_the_full_target(project_root):
    settings = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    settings["outputs"]["rockbox"]["max_tracks"] = 5
    write(project_root / "config/settings.yaml", settings)

    _, results = results_for(project_root)
    assert by_name(results, "Rock", "plex").count > 5


def test_capacity_trim_spreads_across_artists(project_root):
    """A trimmed playlist must not collapse to one artist."""
    settings = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    settings["outputs"]["rockbox"]["max_tracks"] = 6
    write(project_root / "config/settings.yaml", settings)

    _, results = results_for(project_root)
    rock = by_name(results, "Rock", "rockbox")
    assert len({t.artist for t in rock.tracks}) > 1


def test_capacity_trim_is_deterministic(project_root):
    settings = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    settings["outputs"]["rockbox"]["max_tracks"] = 4
    write(project_root / "config/settings.yaml", settings)

    _, first = results_for(project_root)
    _, second = results_for(project_root)
    # strict: a differing result count is itself a determinism failure, and a
    # plain zip would compare the shorter prefix and pass.
    for a, b in zip(first, second, strict=True):
        assert [t.relative_path for t in a.tracks] == [t.relative_path for t in b.tracks]


def test_capacity_removal_is_recorded_for_reporting(project_root):
    settings = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    settings["outputs"]["rockbox"]["max_tracks"] = 3
    write(project_root / "config/settings.yaml", settings)

    _, results = results_for(project_root)
    trimmed = [r for r in results if r.capacity_removed > 0]
    assert trimmed
    assert all(r.capacity_limit == 3 for r in trimmed)


def test_no_capacity_limit_leaves_playlists_intact(project_root):
    """The default project has no rockbox limit configured in the fixture."""
    _, results = results_for(project_root)
    assert all(r.capacity_removed == 0 for r in results)


def test_stale_manifests_are_removed(project_root):
    """Regression: a deleted playlist's manifest survived and `plex sync --apply`
    recreated the playlist from it."""
    manifests = project_root / "output/manifests"
    manifests.mkdir(parents=True)
    stale = manifests / "Deleted Playlist.json"
    stale.write_text("{}", encoding="utf-8")
    generate(project_root)
    assert not stale.exists()
    assert (manifests / "Rock.json").exists()
    summary = json.loads((project_root / "reports/generation-summary.json").read_text())
    assert any(path.endswith("Deleted Playlist.json") for path in summary["stale_files_removed"])


def test_manifest_carries_the_disc_number(project_root):
    export = project_root / "Library Playlist.m3u8"
    export.write_text(
        export.read_text(encoding="utf-8")
        + "M:\\Music\\Soundgarden\\Superunknown\\2-03 Fell On Black Days.m4a\n",
        encoding="utf-8",
    )
    generate(project_root)
    data = json.loads((project_root / "output/manifests/Grunge.json").read_text())
    row = next(t for t in data["tracks"] if t["title"] == "Fell On Black Days")
    assert (row["disc_no"], row["track_no"]) == (2, 3)


# --- definition keys ------------------------------------------------------


def rewrite_playlists(project_root, mutate):
    path = project_root / "taxonomy/playlists.yaml"
    data = yaml.safe_load(path.read_text())
    mutate(data["playlists"])
    write(path, data)


def test_unknown_definition_key_fails_validate_and_generation(project_root, capsys):
    """Regression: `match_any_tag` left the playlist with no tag criterion,
    which selected the whole catalog."""

    def misspell(playlists):
        playlists[1]["match_any_tag"] = playlists[1].pop("match_any_tags")

    rewrite_playlists(project_root, misspell)
    assert validate(project_root) == 1
    assert "Playlist 'Grunge' has unknown key 'match_any_tag'" in capsys.readouterr().out
    with pytest.raises(ValueError, match="Playlist 'Grunge' has unknown key 'match_any_tag'"):
        Project.load(project_root)


def test_unknown_balancing_and_ordering_keys_are_errors(project_root, capsys):
    def misspell(playlists):
        playlists[0]["balancing"] = {"max_share_per_artis": 0.2}
        playlists[2]["ordering"]["seed"] = "unused"

    rewrite_playlists(project_root, misspell)
    assert validate(project_root) == 1
    out = capsys.readouterr().out
    assert "Playlist 'Rock' has unknown balancing key 'max_share_per_artis'" in out
    assert "'R.E.M. Collection' has ordering key 'seed'" in out


def test_playlist_names_sharing_a_filename_fail_validate(project_root, capsys):
    def collide(playlists):
        playlists.append(
            {"name": "Rock?", "type": "genre", "match_any_tags": ["rock"],
             "ordering": {"strategy": "interleaved"}}
        )
        playlists.append(
            {"name": "Rock:", "type": "genre", "match_any_tags": ["rock"],
             "ordering": {"strategy": "interleaved"}}
        )

    rewrite_playlists(project_root, collide)
    assert validate(project_root) == 1
    assert "share the output filename" in capsys.readouterr().out


# --- album overrides ------------------------------------------------------


def test_clear_tags_removes_an_album_from_a_tag_playlist(project_root):
    write(project_root / "profiles/albums.yaml", {
        "albums": {"Pearl Jam": {"Ten": {"clear_tags": ["grunge"]}}}
    })
    _, results = results_for(project_root)
    grunge = by_name(results, "Grunge", "plex")
    assert not any(t.album == "Ten" for t in grunge.tracks)
    assert any(t.album == "Live Bootleg" for t in grunge.tracks)


def test_unknown_clear_tags_and_clear_flags_fail_validate(project_root, capsys):
    write(project_root / "profiles/albums.yaml", {
        "albums": {"Pearl Jam": {"Ten": {"clear_tags": ["grungy"], "clear_flags": ["lve"]}}}
    })
    assert validate(project_root) == 1
    out = capsys.readouterr().out
    assert "Album 'Pearl Jam - Ten' clears undeclared tag 'grungy'" in out
    assert "Album 'Pearl Jam - Ten' clears unknown flag 'lve'" in out
    with pytest.raises(ValueError, match="grungy"):
        Project.load(project_root)


# --- report determinism and popularity coverage ---------------------------


def test_unclassified_artists_tie_break_by_name(project_root):
    export = project_root / "Library Playlist.m3u8"
    extra = "".join(
        f"M:\\Music\\{artist}\\Album\\01 Song.m4a\n" for artist in ("Zeta", "Alpha", "Mid")
    )
    export.write_text(export.read_text(encoding="utf-8") + extra, encoding="utf-8")
    report(project_root)
    data = json.loads((project_root / "reports/audit-report.json").read_text())
    rows = data["taxonomy"]["top_unclassified_artists"]
    assert [r["artist"] for r in rows] == ["Alpha", "Mid", "Zeta"]


def test_report_states_measured_popularity_coverage(project_root):
    report(project_root)
    data = json.loads((project_root / "reports/audit-report.json").read_text())
    assert data["popularity"]["measured_file_loaded"] is False
    assert data["popularity"]["measured_coverage_pct"] == 0.0
    assert data["popularity"]["scored_songs"] > 0
