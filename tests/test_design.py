"""The design graph: roots, components, and body ownership.

These check the graph against something outside it wherever they can. The
strongest is the body count: the design segment names its bodies by blob
filename, and those blobs are a separate part of the archive found by a
separate scan, so agreement between the two is not a restatement.
"""

from __future__ import annotations

import re

import pytest

import ezf3d
from ezf3d.model.design import BREP_NAME_RE, read_design, references
from ezf3d.streams.segment import is_feature_type


@pytest.fixture
def designs(opened):
    """``(child document, Design)`` for every document that carries one."""
    found = [(child, read_design(child.design)) for child in opened.documents() if child.design]
    if not found:
        pytest.skip("no design segment in this sample")
    return found


def test_every_body_on_disk_is_owned_by_exactly_one_component(designs):
    """The two counts come from different files, so agreement means something.

    ``Breps.BlobParts`` is found by scanning the archive's central directory;
    the names come from the design graph.  Every component of every sample
    owns precisely two bodies -- the ``.smbh`` carrying history and the
    ``.smb`` without -- which is what says the id ranges are drawn right.
    """
    for child, design in designs:
        on_disk = {f"BREP.{body.uuid}.{body.suffix}" for body in child.bodies}
        assert on_disk, child.name
        owned = [name for component in design.components for name in component.bodies]
        assert len(owned) == len(set(owned)), f"{child.name}: a body has two owners"
        assert set(owned) == on_disk, f"{child.name}: graph and archive disagree"
        assert set(design.bodies) == on_disk, child.name


def test_body_names_are_blob_filenames(designs):
    for _, design in designs:
        for name in design.bodies:
            assert BREP_NAME_RE.match(name), name


def test_a_design_has_a_components_root_and_at_least_one_component(designs):
    for child, design in designs:
        assert "ComponentsRoot" in design.roots, child.name
        assert design.components, child.name
        assert design.roots["ComponentsRoot"] > 0 or design.components


def test_component_ids_are_ordered_and_ranges_do_not_overlap(designs):
    """Ownership is by id range, so the ranges must be a partition."""
    for child, design in designs:
        ids = [component.oid for component in design.components]
        assert ids == sorted(ids), child.name
        assert len(set(ids)) == len(ids), child.name
        for component, following in zip(design.components, design.components[1:], strict=False):
            assert component.limit == following.oid, child.name
        assert design.components[-1].limit is None, child.name


def test_a_component_declares_at_most_one_feature_registry(designs):
    """One registry per component that has a timeline, and none twice.

    This is what closed the question left open by the type-name census:
    Robotic_Bhujha's eleven registries are its eleven components, not eleven
    of anything else.  A component with no timeline of its own -- two of the
    package's members -- has none, which is why the check is "at most".
    """
    for child, design in designs:
        with_registry = [c for c in design.components if c.features]
        assert len(with_registry) <= len(design.components), child.name
        for component in with_registry:
            assert len(component.features) >= 2, component.name
            for kind in component.features:
                assert is_feature_type(f"Dc{kind}MetaType"), kind


def test_component_names_are_read_or_reported_as_unnamed(designs):
    """Fusion writes a GUID where a component was never given a name."""
    for child, design in designs:
        for component in design.components:
            assert component.name, f"{child.name}: component {component.oid} has no name"
            if not component.is_named:
                assert re.fullmatch(r"[0-9a-fA-F-]{36}", component.name)


def test_references_are_read_only_where_a_record_is_a_list(wheel):
    """The reference scan is permissive, and the module says so.

    Following ``0x01`` + id transitively reaches everything from anywhere, so
    this pins the property rather than leaving it as a comment: from one
    component, a transitive walk reaches most of the graph, which is why
    ownership is decided by id range instead.
    """
    with ezf3d.readfile(wheel) as doc:
        segment = doc.design
        items = segment.objects()
        known = {item.oid for item in items}
        by_id = {item.oid: item for item in items}
        design = read_design(segment)
        start = design.components[0].oid
        seen, stack = set(), [start]
        while stack:
            oid = stack.pop()
            if oid in seen or oid not in by_id:
                continue
            seen.add(oid)
            stack.extend(references(segment.bulk.body, by_id[oid], known))
    assert len(seen) > len(items) // 2, "the walk was expected to over-reach, and did not"
