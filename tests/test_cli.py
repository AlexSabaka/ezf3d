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
