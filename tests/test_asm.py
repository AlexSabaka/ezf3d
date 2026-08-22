"""ASM / SAB reading.

The load-bearing assertion is that every body tokenizes to completion: a
partial walk that silently stops at unfamiliar geometry would still produce a
plausible-looking census, so "consumed the whole file" is the only honest check.
"""

from __future__ import annotations

import pytest

import ezf3d
from ezf3d.asm import parse, read_header
from ezf3d.asm.records import END_MARKER, NULL

#: Topology of SUCKER's plain body, from a complete walk.  These numbers pin
#: the token grammar: a regression that stops early lowers every one of them.
SUCKER_PLAIN = {
    "uuid_prefix": "0f9f9e57",
    "entities": 38623,
    "body": 105,
    "lump": 136,
    "shell": 136,
    "face": 2006,
    "edge": 5306,
    "vertex": 3548,
}


def test_every_body_tokenizes_to_the_end_marker(sample):
    with ezf3d.readfile(sample) as doc:
        bodies = doc.bodies
        assert bodies
        for body in bodies:
            data = body.raw()
            header = read_header(data)
            assert header.product == "Autodesk Neutron"
            assert header.word_size in (4, 8)
            model = parse(data)
            assert len(model) > 0
            # A body that parses without error but never reaches the end
            # marker was only partly understood.
            assert model.terminated, f"{body.uuid}: walk did not reach {END_MARKER}"


def test_section_markers_do_not_swallow_the_entity_behind_them(sucker):
    """``End of ASM History Section`` is a prefix on a real record.

    Treating it as a standalone record loses that entity and shifts every
    index after it.
    """
    with ezf3d.readfile(sucker) as doc:
        body = next(b for b in doc.bodies if b.has_history)
        model = body.model()
    # The record carrying the history-end prefix is a face; it must survive as
    # a face, with the marker stripped rather than the record dropped.
    boundary = max(e.index for e in model.history)
    carrier = next(e for e in model.entities if e.index > boundary)
    assert carrier.base == "face", f"entity after the history block is {carrier.types}"
    assert "Section" not in carrier.types


def test_history_flag_matches_the_file_extension(sample):
    """``.smbh`` is ASM-with-history; the stream should say so too."""
    with ezf3d.readfile(sample) as doc:
        for body in doc.bodies:
            if body.has_history:
                assert body.model().has_history, body.uuid


def test_pointers_resolve_within_the_file(design):
    with ezf3d.readfile(design) as doc:
        model = doc.bodies[0].model()
        for entity in model.entities:
            for target in entity.links():
                assert model.resolve(target) is not None, f"{entity.name} -> {target}"


#: Which base class each positional pointer of a topology record must land on.
#: Resolving in range is not enough -- a wrong-but-in-range index looks
#: identical until you check the type, which is exactly how the history-block
#: indexing bug survived the first release.
POINTER_CONTRACT = {
    "edge": {2: "vertex", 3: "vertex", 5: "curve"},
    "coedge": {2: "coedge", 3: "coedge", 5: "edge", 6: "loop"},
    "loop": {3: "coedge", 4: "face"},
    "face": {3: "loop", 4: "shell", 6: "surface"},
    "shell": {4: "face", 6: "lump"},
    "lump": {3: "shell", 4: "body"},
    "vertex": {2: "edge", 3: "point"},
}

#: Slots ACIS is allowed to leave null.  ``vertex -> edge`` is a convenience
#: back-reference, not a structural link, and a degenerate edge -- a cone apex
#: or sphere pole, where both vertices coincide -- genuinely has no curve.
NULLABLE = {("vertex", 2), ("edge", 5)}


def _may_be_null(base: str, slot: int, pointers: list[int]) -> bool:
    if (base, slot) not in NULLABLE:
        return False
    if base == "edge":
        return len(pointers) > 3 and pointers[2] == pointers[3]  # degenerate
    return True


def test_every_topology_pointer_lands_on_the_right_class(sample):
    """The load-bearing traversal check.

    Pointers index the main-section entity list, which excludes the rollback
    history block and counts marker-prefixed records as the entities they
    carry.  Get any of that wrong and pointers still resolve -- just to the
    wrong things -- so the *type* of the target is what has to be asserted.
    """
    wrong = []
    with ezf3d.readfile(sample) as doc:
        for child in doc.documents():
            for body in child.bodies:
                model = body.model()
                for entity in model.entities:
                    contract = POINTER_CONTRACT.get(entity.base)
                    if contract is None:
                        continue
                    pointers = entity.pointers()
                    for slot, expected in contract.items():
                        if slot >= len(pointers):
                            continue
                        if pointers[slot] == NULL and _may_be_null(entity.base, slot, pointers):
                            continue
                        target = model.resolve(pointers[slot])
                        if target is None or target.base != expected:
                            got = target.name if target else None
                            wrong.append(
                                f"{body.uuid[:8]} {entity.base}#{entity.index} "
                                f"slot {slot}: expected {expected}, got {got}"
                            )
    assert not wrong, f"{len(wrong)} bad pointer(s), first few:\n" + "\n".join(wrong[:5])


def test_history_records_are_kept_out_of_the_pointer_space(sample):
    """``.smbh`` bodies embed a rollback block that pointers do not address."""
    with ezf3d.readfile(sample) as doc:
        with_history = [b for b in doc.bodies if b.has_history]
        if not with_history:
            pytest.skip("no body with rollback history in this sample")
        for body in with_history:
            model = body.model()
            assert model.has_history
            assert model.history, f"{body.uuid}: history flagged but no records captured"
            # The block is delta bookkeeping, not model topology.
            assert {e.base for e in model.history} <= {"delta_state", "history_stream"}
            assert not any(e.base == "face" for e in model.history)


def test_sucker_topology_census_is_stable(sucker):
    with ezf3d.readfile(sucker) as doc:
        body = next(b for b in doc.bodies if b.uuid.startswith(SUCKER_PLAIN["uuid_prefix"]))
        stats = body.census()
    assert stats.entities == SUCKER_PLAIN["entities"]
    for name in ("body", "lump", "shell", "face", "edge", "vertex"):
        assert stats.topology[name] == SUCKER_PLAIN[name], name


def test_analytic_bodies_are_identified(bhujha):
    """Bodies with no spline geometry can be tessellated exactly."""
    with ezf3d.readfile(bhujha) as doc:
        analytic = [b for b in doc.bodies if b.census().analytic_only]
    assert analytic, "this design should contain purely analytic bodies"
    for body in analytic:
        assert body.census().spline_fraction == 0.0


def test_vertex_bounds_are_ordered_and_finite(design):
    with ezf3d.readfile(design) as doc:
        bounds = doc.bodies[0].census().vertex_bounds
    assert bounds is not None
    for lo, hi in zip(bounds.min, bounds.max, strict=True):
        assert lo <= hi
    assert bounds.diagonal > 0


def test_rejects_data_that_is_not_asm():
    from ezf3d.asm.header import AsmError

    with pytest.raises(AsmError):
        read_header(b"not an ASM file at all, really")


def test_entity_queries(wheel):
    """The query surface a caller uses to walk the B-Rep."""
    with ezf3d.readfile(wheel) as doc:
        model = doc.bodies[0].model()

    faces = list(model.of_type("face"))
    assert len(faces) == model.counts()["face"]
    assert all(f.name == "face" for f in faces)

    # of_type also matches on the ASM base class, so every analytic and spline
    # surface is reachable under one name.
    surfaces = list(model.of_type("surface"))
    assert {s.base for s in surfaces} == {"surface"}
    assert len(surfaces) > len(list(model.of_type("plane")))

    plane = next(model.of_type("plane"))
    assert plane.name == "plane" and plane.base == "surface"
    assert len(plane.positions()) == 1  # a plane is a point plus two directions


def test_bounds_convert_to_millimetres(wheel):
    """Fusion's kernel is centimetres; its UI is millimetres."""
    with ezf3d.readfile(wheel) as doc:
        bounds = doc.bodies[0].census().vertex_bounds
    assert bounds is not None
    mm = bounds.as_mm()
    assert mm.size == pytest.approx(tuple(v * 10 for v in bounds.size))
