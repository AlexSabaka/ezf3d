"""The document model and the ``readfile`` entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

import ezf3d
from ezf3d.model.document import Ef3dError


def test_readfile_opens_every_sample(sample):
    with ezf3d.readfile(sample) as doc:
        assert doc.name
        assert doc.manifest.doc_type == "Fusion Document"
        assert doc.primary is not None
        assert doc.bodies


def test_plain_design_is_not_a_package(design):
    with ezf3d.readfile(design) as doc:
        assert not doc.is_package
        assert doc.linked == {}


def test_package_resolves_its_xref_graph(focuser):
    with ezf3d.readfile(focuser) as doc:
        assert doc.is_package
        assert doc.name == "Focuser Mk1"
        assert doc.package.root_path == "67727a39-fa29-49f6-849f-35e15bdf1231.f3d"
        names = {d.name for d in doc.documents()}
        assert names == {"Focuser Mk1", "CRAY_2in drawtube CUSTOM", "Roundified Cray"}
        root = doc.package.root
        assert {c.name for c in doc.package.children(root)} == {
            "CRAY_2in drawtube CUSTOM",
            "Roundified Cray",
        }


def test_bodies_are_not_parsed_until_asked(sucker):
    """`info` must stay cheap on a design holding tens of megabytes of ASM."""
    with ezf3d.readfile(sucker) as doc:
        body = doc.bodies[0]
        assert body._model is None
        assert body.size > 0  # metadata only
        body.model()
        assert body._model is not None


def test_census_is_cached(wheel):
    with ezf3d.readfile(wheel) as doc:
        body = doc.bodies[0]
        assert body.census() is body.census()


def test_preview_is_a_png(sample):
    with ezf3d.readfile(sample) as doc:
        data = doc.preview()
    assert data is not None
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


def test_multiple_asset_folders_are_kept_separate(bhujha):
    """An animation lives beside the design, with its own segments."""
    with ezf3d.readfile(bhujha) as doc:
        assert set(doc.assets) == {"FusionAssetName[Active]", "Animation"}
        animation = doc.assets["Animation"]
        assert animation.state is None
        assert animation.design is not None
        assert animation.manifest.parent_guids


def test_non_fusion_zip_is_rejected(tmp_path: Path):
    import zipfile

    junk = tmp_path / "junk.f3d"
    with zipfile.ZipFile(junk, "w") as zf:
        zf.writestr("hello.txt", "not a design")
    with pytest.raises(Ef3dError):
        ezf3d.readfile(junk)
