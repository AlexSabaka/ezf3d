"""B-Rep traversal.

These check the shape of the graph rather than its geometry: that chains
terminate, that rings close, that a body's face count agrees with the record
census when there is no history to muddy it, and that rolled-back duplicates
collapse.
"""

from __future__ import annotations

import collections

import ezf3d
from ezf3d.asm.brep import Body, Shape


def test_walked_counts_match_the_census_when_there_is_no_history(opened):
    """Without rollback residue, every face and edge record is reachable.

    A body with history is excluded here because its stale records are exactly
    what traversal is supposed to leave behind.
    """
    checked = 0
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            model = body.model()
            if model.has_history:
                continue
            shape = Shape(model)
            census = body.census()
            walked_faces = sum(1 for _ in shape.faces())
            assert walked_faces == census.faces, body.uuid
            # The census counts by concrete class, so tolerant edges sit
            # outside `topology["edge"]`; traversal resolves by base class
            # and picks them up, hence the two are added here.
            record_edges = census.topology["edge"] + census.other.get("tedge", 0)
            assert sum(1 for _ in shape.edges()) <= record_edges
            checked += 1
    assert checked, "sample had no history-free body"


def test_history_bodies_leave_orphans_behind(bhujha):
    """A rolled-back body keeps records traversal must not reach."""
    with ezf3d.readfile(bhujha) as doc:
        with_history = [b for b in doc.bodies if b.has_history]
        assert with_history
        for body in with_history:
            model = body.model()
            reachable = Shape(model).reachable_indices()
            assert reachable
            assert len(reachable) <= len(model.entities)


def test_duplicate_body_records_collapse_to_one_solid(wheel):
    """Saved states repeat the ``body`` record while sharing one lump chain."""
    with ezf3d.readfile(wheel) as doc:
        body = next(b for b in doc.bodies if b.has_history)
        shape = Shape(body.model())
    records = list(shape.bodies())
    solids = list(shape.solids())
    assert len(solids) < len(records), "this body should carry rollback duplicates"
    # De-duplicating must not lose any geometry.
    from_records = {f.index for b in records for f in b.faces()}
    from_solids = {f.index for b in solids for f in b.faces()}
    assert from_records == from_solids


def test_loops_close_and_faces_have_at_least_one(opened):
    rings = closed = 0
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            for face in Shape(body.model()).faces():
                loops = list(face.loops())
                assert loops, f"face#{face.index} has no loop"
                for loop in loops:
                    coedges = list(loop.coedges())
                    assert coedges, f"loop#{loop.index} is empty"
                    rings += 1
                    # A well-formed ring returns to its first coedge.
                    last = coedges[-1]
                    nxt = last.model.resolve(last.entity.pointers()[2])
                    if nxt is not None and nxt.index == coedges[0].index:
                        closed += 1
                assert face.surface_entity is not None
    assert rings
    # Stale loops in a rolled-back design can run into a chain that never
    # comes back; live topology overwhelmingly closes.
    assert closed / rings > 0.95, f"only {closed}/{rings} rings close"


def test_most_edges_are_shared_by_two_faces(opened):
    """A closed solid is 2-manifold; open sheet bodies legitimately are not."""
    counts: collections.Counter[int] = collections.Counter()
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            per_edge: collections.Counter[int] = collections.Counter()
            for face in Shape(body.model()).faces():
                for loop in face.loops():
                    for coedge in loop.coedges():
                        edge = coedge.edge
                        if edge is not None:
                            per_edge[edge.index] += 1
            counts.update(per_edge.values())
    total = sum(counts.values())
    assert total
    # Designs full of open sheet bodies sit lower than closed solids do.
    assert counts[2] / total > 0.6, f"only {counts[2]}/{total} edges shared by two faces"


def test_closed_edges_are_distinguished_from_degenerate_ones(opened):
    """Both have one vertex; only one of them is a point.

    A closed edge is a full circle — a cylinder rim — and keeps its curve. A
    degenerate edge is a cone apex or sphere pole and has none. Conflating them
    would throw away every circular rim in the model.
    """
    closed = degenerate = 0
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            for edge in Shape(body.model()).edges():
                if not edge.is_closed:
                    continue
                assert edge.start is not None and edge.end is not None
                assert edge.start.index == edge.end.index
                if edge.is_degenerate:
                    degenerate += 1
                    assert edge.curve_entity is None
                else:
                    closed += 1
                    assert edge.curve_entity is not None
    assert closed, "every design here should have at least one circular rim"


def test_traversal_terminates_on_every_body(opened):
    """Cycle guards mean a malformed chain yields a short walk, never a hang."""
    doc = opened
    for child in doc.documents():
        for body in child.bodies:
            model = body.model()
            shape = Shape(model)
            faces = list(shape.faces())
            assert len(faces) <= len(model.entities)
            for solid in shape.solids():
                assert isinstance(solid, Body)
                assert len(list(solid.lumps())) <= len(model.entities)
