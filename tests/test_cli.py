"""The CLI's JSON contract.

Agents parse ``--json``; these tests pin the envelope and the fields a caller
would branch on.
"""

from __future__ import annotations

import json
import zipfile
import zlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ezf3d.cli import app

runner = CliRunner()


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.stdout


def payload(*args: str):
    code, out = invoke(*args, "--json")
    assert code == 0, out
    envelope = json.loads(out)
    assert envelope["ok"] is True
    return envelope


def test_version():
    code, out = invoke("--version")
    assert code == 0
    assert "ezf3d" in out


def test_info_reports_identity_and_segments(wheel):
    data = payload("info", str(wheel))["data"]
    assert data["doc_type"] == "Fusion Document"
    assert data["extension"] == ".f3d"
    assert data["schema_versions"]["SimCommon"] == 30005
    assert data["kernel"]["versions"] == ["232.4.0.65535"]
    assert data["kernel"]["word_sizes"] == [8]
    assert data["compression"]["zstd"] > 0
    assert data["totals"]["bodies"] == 2

    segments = {s["type"]: s for s in data["assets"][0]["segments"]}
    assert set(segments) == {
        "FusionDesignSegmentType",
        "FusionACTSegmentType",
        "FusionBrowserSegmentType",
    }
    assert segments["FusionDesignSegmentType"]["is_design"] is True


def test_info_on_a_package_includes_linked_documents(focuser):
    data = payload("info", str(focuser))["data"]
    assert data["package"]["root"].endswith(".f3d")
    assert {d["name"] for d in data["linked"]} == {
        "CRAY_2in drawtube CUSTOM",
        "Roundified Cray",
    }


def test_bodies_reports_topology(sucker):
    rows = payload("bodies", str(sucker))["data"]
    plain = next(r for r in rows if r["uuid"].startswith("0f9f9e57"))
    assert plain["entities"] == 38623
    assert plain["topology"]["face"] == 2006
    assert plain["word_size"] == 8
    assert plain["referenced_by_design"] is True
    assert plain["vertex_bounds"]["unit"] == "cm"


def test_bodies_filter_by_uuid(sucker):
    rows = payload("bodies", str(sucker), "--body", "0f9f9e57")["data"]
    assert len(rows) == 1


def test_bodies_unknown_uuid_fails_cleanly(sucker):
    code, out = invoke("bodies", str(sucker), "--body", "deadbeef", "--json")
    assert code == 1
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["error"]


def test_tree_lists_assets_and_bodies(bhujha):
    data = payload("tree", str(bhujha))["data"]
    folders = {a["folder"] for a in data["assets"]}
    assert folders == {"FusionAssetName[Active]", "Animation"}
    design = next(a for a in data["assets"] if a["folder"] == "FusionAssetName[Active]")
    assert len(design["bodies"]) == 22


def test_dump_reproduces_the_archive_exactly(bhujha, tmp_path: Path):
    """Every extracted file must match the CRC-32 the archive itself declares,
    and the extracted tree must have exactly the archive's paths -- no more,
    no fewer."""
    out = tmp_path / "out"
    data = payload("dump", str(bhujha), "--out", str(out))["data"]
    assert data["files"] > 0
    assert data["directories"] > 0

    expected_files, expected_dirs = set(), set()
    with zipfile.ZipFile(bhujha) as zf:
        for info in zf.infolist():
            if info.is_dir():
                expected_dirs.add(info.filename.rstrip("/"))
                continue
            expected_files.add(info.filename)
            written = (out / info.filename).read_bytes()
            assert zlib.crc32(written) == info.CRC, info.filename
            assert len(written) == info.file_size

    on_disk = {
        str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p.name != ".DS_Store"
    }
    assert on_disk == expected_files
    assert expected_dirs <= {str(p.relative_to(out)) for p in out.rglob("*") if p.is_dir()}


def test_thumb_writes_a_png(sucker, tmp_path: Path):
    out = tmp_path / "thumb.png"
    data = payload("thumb", str(sucker), "--out", str(out))["data"]
    assert out.read_bytes().startswith(b"\x89PNG")
    assert data["bytes"] == out.stat().st_size


def test_raw_lists_entries_then_tokenizes_a_body(sucker):
    entries = payload("raw", str(sucker))["data"]
    body = next(e for e in entries if e["name"].endswith(".smb"))
    assert body["method"] == "zstd"

    dump = payload("raw", str(sucker), body["name"], "--limit", "3")["data"]
    assert dump["mode"] == "tokens"
    assert any("asmheader" in line for line in dump["lines"])


def test_raw_rejects_an_unknown_mode(sucker):
    code, out = invoke("raw", str(sucker), "Manifest.dat", "--mode", "wat", "--json")
    assert code == 1
    assert json.loads(out)["ok"] is False


@pytest.mark.parametrize("command", ["info", "tree", "bodies"])
def test_commands_render_for_humans(command, wheel):
    code, out = invoke(command, str(wheel))
    assert code == 0
    assert out.strip()


def test_render_writes_a_png_and_reports_what_it_drew(bhujha, tmp_path: Path):
    out = tmp_path / "render.png"
    data = payload("render", str(bhujha), "--out", str(out), "--view", "front")["data"]
    assert out.read_bytes().startswith(b"\x89PNG")
    assert data["bytes"] == out.stat().st_size
    assert data["size"] == [1024, 768]
    assert data["segments"] > 0
    assert data["ink_bounds"] is not None
    # Spline curves are evaluated now, so almost nothing is left out; what
    # remains is reported rather than faked.
    assert data["omitted"] * 20 < data["polylines"]
    assert data["chord_approximated"] == 0


def test_render_chords_flag_adds_the_omitted_edges(sucker, tmp_path: Path):
    plain = payload("render", str(sucker), "--out", str(tmp_path / "a.png"))["data"]
    chorded = payload("render", str(sucker), "--out", str(tmp_path / "b.png"), "--chords")["data"]
    assert plain["omitted"] > 0
    assert chorded["chord_approximated"] == plain["omitted"]
    assert chorded["polylines"] > plain["polylines"]


def test_render_accepts_a_single_body(wheel, tmp_path: Path):
    import ezf3d as _ezf3d

    with _ezf3d.readfile(wheel) as doc:
        uuid = doc.bodies[0].uuid[:8]
    data = payload("render", str(wheel), "--out", str(tmp_path / "one.png"), "--body", uuid)["data"]
    assert data["bodies"] == 1


def test_render_turntable_makes_a_contact_sheet(wheel, tmp_path: Path):
    out = tmp_path / "sheet.png"
    data = payload(
        "render", str(wheel), "--out", str(out), "--turntable", "4", "--size", "600x400"
    )["data"]
    assert data["frames"] == 4
    # Four frames in a 3-wide grid is two rows.
    assert data["size"] == [600, 400]


def test_render_rejects_a_bad_size(wheel, tmp_path: Path):
    code, out = invoke(
        "render", str(wheel), "--out", str(tmp_path / "x.png"), "--size", "huge", "--json"
    )
    assert code == 1
    assert json.loads(out)["ok"] is False


def test_mesh_reports_what_it_built(wheel):
    data = payload("mesh", str(wheel))["data"]
    assert data["triangles"] > 0
    assert data["faces_meshed"] > 0
    assert data["solids"] > 0
    # Faces straying more than four times past the tolerance are reported
    # rather than meshed, so nothing in the mesh may exceed that.
    assert data["max_deviation_cm"] <= data["tolerance_cm"] * 4
    assert data["bounds_cm"]["min"] < data["bounds_cm"]["max"]
    # Spline faces are named, not silently missing.
    assert set(data["unsupported"]) or data["faces_skipped"] == 0


def test_mesh_tolerance_changes_the_density(wheel):
    coarse = payload("mesh", str(wheel), "--tolerance", "0.05")["data"]
    fine = payload("mesh", str(wheel), "--tolerance", "0.005")["data"]
    assert fine["triangles"] > coarse["triangles"]
    assert fine["max_deviation_cm"] < coarse["max_deviation_cm"]


def test_export_writes_each_format(wheel, tmp_path: Path):
    for fmt, suffix in (("stl", "stl"), ("obj", "obj"), ("glb", "glb")):
        out = tmp_path / f"m.{suffix}"
        data = payload("export", str(wheel), "--out", str(out), "-f", fmt)["data"]
        assert data["bytes"] == out.stat().st_size
        assert data["format"] == fmt
        assert data["triangles"] > 0


def test_export_rejects_an_unknown_format(wheel, tmp_path: Path):
    code, out = invoke("export", str(wheel), "--out", str(tmp_path / "m.x"), "-f", "step", "--json")
    assert code == 1
    assert "unknown format" in json.loads(out)["error"]


def test_export_unit_option(wheel, tmp_path: Path):
    code, out = invoke(
        "export", str(wheel), "--out", str(tmp_path / "m.obj"), "--unit", "furlong", "--json"
    )
    assert code == 1
    assert json.loads(out)["ok"] is False


def test_shaded_render_draws_triangles(wheel, tmp_path: Path):
    out = tmp_path / "shaded.png"
    data = payload("render", str(wheel), "--out", str(out), "--shaded", "--size", "320x240")["data"]
    assert data["shaded"] is True
    assert data["triangles"] > 0
    assert out.read_bytes().startswith(b"\x89PNG")
    assert data["ink_bounds"] is not None


def test_ogs_reports_what_fusion_cached(wheel):
    data = payload("ogs", str(wheel))["data"]
    assert data["faces"] == 423
    assert data["edges"] == 1006
    assert data["triangles"] > 10_000
    assert data["body"].startswith("068db28d")
    assert data["covers_body"] is True
    # The blob is read whole, and every cached corner is a B-Rep vertex.
    assert (data["blob_gap_bytes"], data["blob_overlap_bytes"]) == (0, 0)
    assert data["corner_coverage"] == 1.0


def test_ogs_on_a_design_without_one_fails_cleanly(bhujha):
    code, out = invoke("ogs", str(bhujha), "--json")
    assert code == 1
    assert json.loads(out)["ok"] is False


def test_ogs_renders_for_humans(wheel):
    code, out = invoke("ogs", str(wheel))
    assert code == 0
    assert "cached triangles" in out


def test_mesh_from_the_cache_skips_tessellation(wheel):
    """``--source ogs`` reports the cache, not a tessellation."""
    data = payload("mesh", str(wheel), "--source", "ogs")["data"]
    assert data["triangles"] == 14984
    assert "faces_meshed" not in data
    assert data["covers_body"] is True


def test_auto_declines_a_cache_that_covers_part_of_a_body(sucker, tmp_path: Path):
    """SUCKER's cache holds 608 faces of 2,006, so ``auto`` does not use it.

    Checked through a wireframe render, which exercises the same choice
    without paying for a tessellation: had the cache been taken, the drawing
    would be its 1,579 cached edges rather than the B-Rep's thousands.
    """
    assert payload("ogs", str(sucker))["data"]["covers_body"] is False
    out = tmp_path / "auto.png"
    data = payload(
        "render", str(sucker), "--out", str(out), "--source", "auto", "--tolerance", "1.0"
    )["data"]
    assert data["source"] == "auto"
    assert data["polylines"] > 5000, "auto drew the partial cache"


def test_export_from_the_cache(wheel, tmp_path: Path):
    out = tmp_path / "cached.stl"
    data = payload("export", str(wheel), "--out", str(out), "--source", "ogs")["data"]
    assert out.stat().st_size == data["bytes"]
    assert data["triangles"] == 14984


def test_source_rejects_an_unknown_value(wheel):
    code, out = invoke("mesh", str(wheel), "--source", "wat", "--json")
    assert code == 1
    assert json.loads(out)["ok"] is False


def test_components_reports_the_tree_and_its_bodies(bhujha):
    data = payload("components", str(bhujha))["data"]
    (document,) = data["documents"]
    assert document["document"] == "Robotic_Bhujha"
    assert document["objects"] > 10_000
    assert "ComponentsRoot" in document["roots"]
    names = [component["name"] for component in document["components"]]
    assert "BASE" in names and "ARM_1" in names
    # Named by the graph, counted in the archive — two different files.
    assert document["bodies_named"] == document["bodies_on_disk"] == 22
    assert all(len(component["bodies"]) == 2 for component in document["components"])


def test_components_covers_every_member_of_a_package(focuser):
    data = payload("components", str(focuser))["data"]
    assert len(data["documents"]) == 3
    for document in data["documents"]:
        assert document["bodies_named"] == document["bodies_on_disk"]


def test_components_renders_for_humans(wheel):
    code, out = invoke("components", str(wheel))
    assert code == 0
    assert "bodies named by the graph" in out


def test_params_reports_names_units_and_values(bhujha):
    data = payload("params", str(bhujha))["data"]
    (document,) = data["documents"]
    assert document["declared"] == len(document["parameters"]) > 400
    assert not document["unreadable"]
    assert not document["literals_disagreeing"]
    assert document["manager"] == document["table"] - 1
    first = document["parameters"][0]
    assert first["name"] == "d1"
    assert first["expression"] == "300 mm"
    # Stored in centimetres, shown in the unit the designer typed.
    assert first["value"] == 30.0
    assert first["display"] == 300.0
    assert first["component"] == "Robotic_Bhujha"


def test_params_filters_by_component(bhujha):
    code, out = invoke("params", str(bhujha), "--component", "jaw", "--limit", "3")
    assert code == 0
    assert "jaw" in out
    assert "more (--limit 0 for all)" in out


def test_params_says_so_when_a_member_has_none(focuser):
    data = payload("params", str(focuser))["data"]
    assert len(data["documents"]) == 3
    empty = [row for row in data["documents"] if not row["parameters"]]
    assert empty
    for row in empty:
        assert row["declared"] == 0 and row["table"] == 0


def test_timeline_reports_features_in_order(sucker):
    data = payload("timeline", str(sucker))["data"]
    (document,) = data["documents"]
    assert len(document["entries"]) == 58
    assert not document["unknown_labels"]
    assert not document["over_counter"]
    assert [entry["index"] for entry in document["entries"]] == list(range(58))
    tenth = document["entries"][9]
    assert tenth["name"] == "Mirror" and tenth["kind"] == "MirrorPattern"
    # Created after most of the design, tenth in the timeline.
    assert tenth["oid"] > document["entries"][10 - 2]["oid"]
    # The registry counts what was ever issued, so it exceeds what is live.
    assert sum(document["declared"].values()) == 83


def test_timeline_kinds_view_shows_live_against_issued(sucker):
    code, out = invoke("timeline", str(sucker), "--kinds")
    assert code == 0
    assert "live" in out and "issued" in out
    assert "Sketch" in out


def test_timeline_says_so_when_a_member_has_none(focuser):
    data = payload("timeline", str(focuser))["data"]
    empty = [row for row in data["documents"] if not row["entries"]]
    assert empty
    for row in empty:
        assert row["oid"] == 0 and not row["declared"]


def test_sketches_reports_the_profile_geometry(sucker):
    data = payload("sketches", str(sucker))["data"]
    (document,) = data["documents"]
    assert len(document["sketches"]) == 8
    assert (document["points"], document["curves"]) == (231, 163)
    assert document["unowned"] == 0
    # The 0.5 mm slot, the case checkable by hand end to end.
    slot = next(row for row in document["sketches"] if row["oid"] == 10251)
    assert slot["index"] == 30
    assert slot["dimensions_checked"] == 1 and not slot["dimensions_missing"]
    # The stored numbers carry Fusion's own constraint-solve noise —
    # -1.9500000000000017 — which the dimension check absorbs and this rounds.
    corners = {(round(x, 9), round(y, 9)) for x, y in slot["coordinates"]}
    assert {(-1.0, -2.0), (1.0, -2.0), (-1.0, -1.95), (1.0, -1.95)} <= corners


def test_sketches_types_its_curves_and_closes_its_loops(sucker):
    data = payload("sketches", str(sucker))["data"]
    (document,) = data["documents"]
    assert document["kinds"] == {"Arc": 54, "Circle": 3, "Line": 106}
    assert sum(document["kinds"].values()) == document["curves"] == 163
    assert (document["loops"], document["loose"]) == (28, 44)
    # The slot sketch: two loops, the outer rectangle and the slot itself.
    slot = next(row for row in document["sketches"] if row["oid"] == 10251)
    assert {frozenset(loop) for loop in slot["loops"]} == {
        frozenset({10299, 10302, 10305, 10306}),
        frozenset({10315, 10316, 10317, 10318}),
    }
    # Every circle and arc agreed with the geometry its own points describe.
    assert not any(row["geometry_disagreeing"] for row in document["sketches"])
    assert sum(row["geometry_checked"] for row in document["sketches"]) == 57


def test_sketches_points_view_lists_coordinates(sucker):
    code, out = invoke("sketches", str(sucker), "--points", "--limit", "2")
    assert code == 0
    assert "more (--limit 0 for all)" in out
    assert "(0, 0)" in out


def test_sketches_place_view_reports_frames_and_their_evidence(sucker):
    data = payload("sketches", str(sucker), "--place")["data"]
    (document,) = data["documents"]
    assert document["planar_faces"] == 1178
    assert document["placed"] == 5 and document["unplaced"] == 3
    assert document["placements"]
    for found in document["placements"]:
        # The solve never asks for orthonormal axes; that it gets them is the check.
        assert found["orthonormality"] < 1e-9
        assert found["residual"] < 1e-7
        assert len(found["normal"]) == 3
    slot = next(f for f in document["placements"] if f["sketch"] == 10251)
    assert slot["normal"] == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)
    assert slot["candidates"] > 1


def test_sketches_does_not_place_unless_asked(sucker):
    """Placement parses every body, so it stays off the default path."""
    data = payload("sketches", str(sucker))["data"]
    (document,) = data["documents"]
    assert document["placements"] == []
    assert (document["placed"], document["unplaced"], document["planar_faces"]) == (0, 0, 0)


def test_sketches_counts_geometry_a_member_has_no_sketch_for(focuser):
    """A registry-less member holds entity records and no feature to own them."""
    data = payload("sketches", str(focuser))["data"]
    barren = [row for row in data["documents"] if not row["sketches"]]
    assert barren
    assert any(row["unowned"] for row in barren), "geometry with no sketch must be counted"


def test_components_reports_material_assignments(sucker):
    data = payload("components", str(sucker))["data"]
    (document,) = data["documents"]
    # One per component plus one per body under BodiesRoot.
    assert document["assignments"] == 13
    assert not document["undeclared_assets"]
    assert set(document["material_assets"].values()) == {"physicalmaterial"}
    (component,) = document["components"]
    assert component["appearance"] == "PrismMaterial-018"
    assert component["body_materials"] == 12


def test_components_materials_view_names_a_user_library(focuser):
    code, out = invoke("components", str(focuser), "--materials")
    assert code == 0
    assert "i4 Custom" in out and "PrismMaterial-018" in out
    assert "declared by the .protein package" in out


def test_timeline_inputs_names_what_drives_each_feature(sucker):
    data = payload("timeline", str(sucker))["data"]
    (document,) = data["documents"]
    extrude = document["entries"][3]
    assert extrude["kind"] == "ExtrudeFeature"
    roles = {p["role"]: p["expression"] for p in extrude["parameters"]}
    assert roles == {"AlongDistance": "-50 mm", "TaperAngle": "0.0 deg"}
    # A kind with no number to carry says so by carrying none.
    paste = next(e for e in document["entries"] if e["kind"] == "PasteBodies")
    assert paste["parameters"] == []


def test_timeline_inputs_renders_for_humans(sucker):
    code, out = invoke("timeline", str(sucker), "--inputs", "--limit", "8")
    assert code == 0
    assert "drives" in out and "AlongDistance" in out
    assert "carry at least one parameter" in out


def test_timeline_says_what_an_extrude_does(sucker):
    data = payload("timeline", str(sucker))["data"]
    (document,) = data["documents"]
    extrudes = {
        e["index"] + 1: (e["operation"], e["direction"])
        for e in document["entries"]
        if e["kind"] == "ExtrudeFeature"
    }
    # The Fusion readout, by timeline position.
    assert extrudes == {
        4: ("Cut", "OneSide"),
        6: ("Cut", "OneSide"),
        27: ("NewBody", "Symmetric"),
        29: ("Cut", "OneSide"),
        32: ("Cut", "OneSide"),
        41: ("Join", "OneSide"),
        45: ("Join", "OneSide"),
        57: ("Join", "OneSide"),
    }
    sketch = next(e for e in document["entries"] if e["kind"] == "Sketch")
    assert sketch["operation"] == "" and sketch["direction"] == ""


def test_timeline_inputs_shows_the_operation(sucker):
    code, out = invoke("timeline", str(sucker), "--inputs", "--limit", "0")
    assert code == 0
    assert "does" in out and "Cut · OneSide" in out
