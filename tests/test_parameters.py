"""Design parameters: names, roles, units, expressions and values.

Nothing here is checked against Fusion — there is no ground truth on hand for
that. What there is instead is redundancy: Fusion writes each parameter's
identity three times over, in three different shapes, and a wrong reading
breaks the agreement between them. These tests are those three checks, plus
the unit one, which re-derives a stored value from a string the designer
typed.
"""

from __future__ import annotations

import pytest

from ezf3d.model.parameters import (
    AUTO_NAME_RE,
    LITERAL_RE,
    NUMBER_AT,
    UNIT_FACTORS,
    Parameters,
    find_table,
    read_parameters,
)
from ezf3d.streams.primitives import scan_strings


def test_every_declared_parameter_reads(parameter_sets):
    """The table declares a count; each name it declares must resolve.

    A record is kept only when the name inside it is the name the table filed
    it under, so this is really two checks: the record parsed, and it parsed
    as the right parameter.
    """
    for child, parameters in parameter_sets:
        assert not parameters.unreadable, f"{child.name}: {parameters.unreadable[:5]}"
        assert len(parameters.values) == parameters.declared, child.name


def test_the_number_field_repeats_the_number_in_the_name(parameter_sets):
    """``dN`` carries N again as a ``u32`` at a fixed offset.

    Two fields written independently by Fusion, so agreement says the preamble
    is being read at the right offset — the one thing the fixed-offset parse
    depends on.
    """
    for child, parameters in parameter_sets:
        for parameter in parameters:
            match = AUTO_NAME_RE.fullmatch(parameter.name)
            if match is None:
                continue
            assert int(match.group(1)) == parameter.number, f"{child.name}: {parameter.name}"


def test_every_record_points_back_to_the_same_manager(parameter_sets):
    """One manager per document, and it sits immediately before the table.

    Read wrongly, the back-reference would land on whatever follows the value
    and scatter; that it is one object for all 695 of the package's parameters
    is what says the record's tail is framed correctly.
    """
    for child, parameters in parameter_sets:
        if not parameters.values:
            continue
        assert parameters.manager, f"{child.name}: records disagree about their manager"
        assert parameters.manager == parameters.table - 1, child.name


def test_literal_expressions_agree_with_the_stored_value(parameter_sets):
    """Values are centimetres and radians — derived, not assumed.

    ``300 mm`` is stored as 30.0 and ``180.0 deg`` as pi. The expression is a
    string the designer typed and the value is a ``f64`` Fusion computed, so
    re-deriving one from the other is a real check on both the unit table and
    the field order.
    """
    for child, parameters in parameter_sets:
        checked, disagreeing = parameters.literal_check()
        assert not disagreeing, f"{child.name}: {disagreeing[:5]}"
        if parameters.values:
            assert checked, f"{child.name}: nothing to check"


def test_display_converts_only_units_it_has_a_factor_for(parameter_sets):
    for _, parameters in parameter_sets:
        for parameter in parameters:
            if parameter.unit in UNIT_FACTORS:
                assert parameter.display is not None
            else:
                assert parameter.display is None


def test_parameters_are_returned_in_creation_order(parameter_sets):
    """The table's own order is not creation order; the reader's is.

    Ids ascend with creation, which is the rule component ownership rests on,
    so parameters are sorted by id rather than left in table order.
    """
    for child, parameters in parameter_sets:
        ids = [parameter.oid for parameter in parameters]
        assert ids == sorted(ids), child.name
        assert len(set(ids)) == len(ids), child.name


def test_names_are_distinct(parameter_sets):
    for child, parameters in parameter_sets:
        assert len(parameters.by_name()) == len(parameters.values), child.name


def test_every_parameter_falls_inside_a_component(parameter_sets, read_design_cached):
    """Attribution reuses the id-range rule the body mapping is checked on."""
    for child, parameters in parameter_sets:
        if not parameters.values:
            continue
        design = read_design_cached(child.design)
        for parameter in parameters:
            owner = design.owner(parameter.oid)
            assert owner is not None, f"{child.name}: {parameter.name} has no component"


def test_a_design_with_no_parameters_reports_none_rather_than_guessing(focuser, shared_document):
    """Two of the package's members hold imported bodies and no parameters.

    The table is found by shape, so "no table" has to mean no parameters
    rather than a failed search: these two members carry no parameter-like
    string at all.
    """
    document = shared_document(focuser)
    empty = [
        child
        for child in document.documents()
        if child.design and not read_parameters(child.design).values
    ]
    assert empty, "expected at least one member with no parameters"
    for child in empty:
        assert find_table(child.design) is None
        # Anchored on real strings, not on raw bytes: a byte sequence that
        # happens to spell ``d32`` turns up in these streams and means nothing.
        names = [
            found.value
            for found in scan_strings(child.design.bulk.body, min_len=1)
            if found.kind == "wstr" and AUTO_NAME_RE.fullmatch(found.value)
        ]
        assert not names, f"{child.name}: {names[:5]}"


def test_the_table_is_found_by_shape_not_by_position(design, shared_document):
    """Its object id differs per document; what identifies it is the walk."""
    segment = shared_document(design).design
    found = find_table(segment)
    assert found is not None
    table, entries = found
    assert len(entries) >= 1
    assert all(oid in {item.oid for item in segment.objects()} for _, oid in entries)
    assert all(name for name, _ in entries)
    assert table.offset + NUMBER_AT < table.end


def test_a_cross_referencing_expression_matches_the_parameter_it_names(focuser, shared_document):
    """``d155`` is written as ``d154``, and holds ``d154``'s value.

    ezf3d does not evaluate expressions; this pins that the two records are
    nonetheless consistent, which a mis-framed read would break.
    """
    document = shared_document(focuser)
    for child in document.documents():
        if not child.design:
            continue
        values = read_parameters(child.design).by_name()
        if "d155" not in values:
            continue
        assert values["d155"].expression == "d154"
        assert values["d155"].value == pytest.approx(values["d154"].value)
        # An inch formula, which is the only thing pinning the ``in`` factor.
        assert values["d143"].expression == "1.5 in / 2"
        assert values["d143"].value == pytest.approx(1.5 * 2.54 / 2)
        return
    pytest.skip("the package member with formulas is not available")


def test_literal_re_does_not_match_a_formula():
    assert LITERAL_RE.fullmatch("300 mm")
    assert LITERAL_RE.fullmatch("-1 mm")
    assert LITERAL_RE.fullmatch("2")
    assert not LITERAL_RE.fullmatch("13 mm - 5 mm")
    assert not LITERAL_RE.fullmatch("d154")


def test_an_empty_parameter_set_is_falsy_and_iterable():
    empty = Parameters()
    assert len(empty) == 0
    assert list(empty) == []
    assert empty.literal_check() == (0, ())
