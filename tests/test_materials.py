"""Material assignments, and the ``.protein`` package they point into.

The strong checks here reach across files. The design stream names an asset by
id; the nested ``.protein`` ZIP declares that id in its table of contents and
carries the appearance name as well. Those are two different members of the
archive, found by two different scans, so their agreement says the assignment
is being read rather than invented.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from ezf3d.inspect import read_materials
from ezf3d.model.design import read_design, references
from ezf3d.model.materials import (
    ANCHOR,
    ASSET_ID_RE,
    PHYSICAL,
    SLOTS,
    Assignment,
    Materials,
    read_assignments,
    read_catalogue,
)


@pytest.fixture
def material_sets(opened, sample, _design_cache):
    """``(child document, Materials)`` for every document with a design."""
    key = ("materials", sample)
    if key not in _design_cache:
        _design_cache[key] = [
            (child, read_materials(child)) for child in opened.documents() if child.design
        ]
    if not _design_cache[key]:
        pytest.skip("no design segment in this sample")
    return _design_cache[key]


def test_every_asset_named_is_one_the_package_declares(material_sets):
    """Design stream against nested ZIP — different files, so a real check."""
    for child, materials in material_sets:
        assert materials.catalogue, f"{child.name}: no protein catalogue"
        assert not materials.check(), f"{child.name}: {materials.check()}"


def test_every_assignment_names_a_physical_material(material_sets):
    """The asset is always the physical material; the appearance travels by name.

    Worth pinning because the package declares appearance assets too, and a
    misread of the first slot would be very likely to land on one.
    """
    for child, materials in material_sets:
        for assignment in materials:
            assert materials.catalogue[assignment.asset] == PHYSICAL, (
                f"{child.name}: {assignment.asset}"
            )


def test_the_appearance_name_appears_in_the_package(material_sets):
    """The fourth slot is a name the ``.protein`` package also carries."""
    for child, materials in material_sets:
        names = {a.appearance for a in materials if a.appearance}
        if not names:
            continue
        carried: set[bytes] = set()
        for asset in child.assets.values():
            for path in asset.layout.proteins:
                package = zipfile.ZipFile(io.BytesIO(asset.raw(path)))
                for entry in package.namelist():
                    data = package.read(entry)
                    carried |= {name.encode() for name in names if name.encode() in data}
        assert carried == {name.encode() for name in names}, child.name


def test_only_components_and_bodies_carry_an_assignment(material_sets):
    """The holders partition, which is what says the anchor is not matching noise.

    Every component has exactly one, and the rest are the objects
    ``BodiesRoot`` lists.  The wheel is the only exception, and only because
    it has no ``BodiesRoot`` at all.
    """
    for child, materials in material_sets:
        design = read_design(child.design)
        holders = {assignment.oid for assignment in materials}
        components = {component.oid for component in design.components}
        assert components <= holders, f"{child.name}: a component has no material"
        root = design.roots.get("BodiesRoot")
        if root is None:
            continue
        segment = child.design
        by_id = {item.oid: item for item in segment.objects()}
        bodies = set(references(segment.bulk.body, by_id[root], set(by_id)))
        assert holders - components <= bodies, f"{child.name}: an outsider carries one"


def test_one_assignment_per_object(material_sets):
    for child, materials in material_sets:
        oids = [assignment.oid for assignment in materials]
        assert len(set(oids)) == len(oids), child.name


def test_asset_ids_look_like_asset_ids(material_sets):
    for _, materials in material_sets:
        for assignment in materials:
            assert ASSET_ID_RE.fullmatch(assignment.asset)


def test_a_user_library_material_is_named_in_the_design(focuser, shared_document):
    """``i4 Custom Materials|PLA`` — the one sample where the name is in the design.

    An Autodesk-library material writes a library guid in that slot instead and
    keeps its readable name inside the ``.protein`` package, which is not read.
    """
    named = [
        assignment
        for child in shared_document(focuser).documents()
        if child.design
        for assignment in read_materials(child)
        if assignment.is_named
    ]
    assert named, "expected a user-library material in the package"
    assert all(assignment.material == "i4 Custom Materials|PLA" for assignment in named)
    assert all(not assignment.library for assignment in named)


def test_an_autodesk_library_material_names_a_library_instead(sucker, shared_document):
    for assignment in read_materials(shared_document(sucker)):
        assert not assignment.material
        assert ASSET_ID_RE.fullmatch(assignment.library)


def test_the_anchor_alone_is_not_enough(sucker, shared_document):
    """Zero padding matches the anchor too, so the slots have to be checked.

    SUCKER's stream holds 22 anchors and 13 assignments; the other nine read
    as runs of empty strings.  This pins the filter that separates them.
    """
    segment = shared_document(sucker).design
    body = segment.bulk.body
    anchors = body.count(ANCHOR)
    assignments = read_assignments(segment)
    assert anchors > len(assignments)
    assert all(assignment.asset for assignment in assignments)
    assert SLOTS == 4


def test_reading_a_broken_package_gives_nothing_rather_than_raising():
    assert read_catalogue(b"not a zip") == {}
    assert read_catalogue(b"") == {}


def test_check_is_not_vacuous_without_a_catalogue():
    """An unread package must not make the cross-check pass by default."""
    assignment = Assignment(oid=1, asset="0C7D1000-E2AC-D0B5-40B5-F6DFEEDF746D")
    blind = Materials(assignments=[assignment])
    assert blind.check() == ()
    assert not blind.catalogue
    seeing = Materials(assignments=[assignment], catalogue={"something-else": PHYSICAL})
    assert seeing.check() == (assignment.asset,)
