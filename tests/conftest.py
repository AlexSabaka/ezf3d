"""Shared fixtures.

The sample designs in ``data/`` are not committed (they are ~24 MB of someone's
real CAD work), so every test that needs one skips when it is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ezf3d

DATA = Path(__file__).resolve().parent.parent / "data"

#: The four sample documents this parser was developed against.
WHEEL = DATA / "Mk1 Focuser, Wheel 2.f3d"
BHUJHA = DATA / "Robotic_Bhujha.f3d"
SUCKER = DATA / "SUCKER.f3d"
FOCUSER = DATA / "Focuser Mk1.f3z"

DESIGNS = [WHEEL, BHUJHA, SUCKER]
ALL_SAMPLES = [*DESIGNS, FOCUSER]


def require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"sample not available: {path.name}")
    return path


@pytest.fixture(params=ALL_SAMPLES, ids=lambda p: p.stem)
def sample(request: pytest.FixtureRequest) -> Path:
    """Every sample document, one per test run."""
    return require(request.param)


@pytest.fixture(params=DESIGNS, ids=lambda p: p.stem)
def design(request: pytest.FixtureRequest) -> Path:
    """Only the plain ``.f3d`` documents."""
    return require(request.param)


@pytest.fixture
def wheel() -> Path:
    return require(WHEEL)


@pytest.fixture
def sucker() -> Path:
    return require(SUCKER)


@pytest.fixture
def bhujha() -> Path:
    return require(BHUJHA)


@pytest.fixture
def focuser() -> Path:
    return require(FOCUSER)


@pytest.fixture(scope="session")
def _open_documents():
    """One open :class:`Document` per sample, shared across the whole session.

    Parsing an ASM body is the expensive part of this suite — the samples hold
    ~100 MB of it — and :meth:`Body.model` caches per document.  Re-opening for
    every test would re-parse everything each time; sharing keeps the suite
    roughly an order of magnitude faster.  Tests only read.
    """
    documents: dict[Path, object] = {}
    yield documents
    for document in documents.values():
        document.close()


@pytest.fixture
def opened(sample: Path, _open_documents):
    """The shared, already-parsed document for the current sample."""
    if sample not in _open_documents:
        _open_documents[sample] = ezf3d.readfile(sample)
    return _open_documents[sample]


@pytest.fixture
def shared_document(_open_documents):
    """Look up any sample's already-parsed document by path.

    For tests that name one sample rather than sweeping all of them: opening
    it afresh would re-parse its bodies, and the ``.f3z`` alone holds sixteen.
    """

    def get(path: Path):
        if path not in _open_documents:
            _open_documents[path] = ezf3d.readfile(path)
        return _open_documents[path]

    return get


@pytest.fixture
def opened_design(design: Path, _open_documents):
    """As :func:`opened`, but only for the plain ``.f3d`` samples."""
    if design not in _open_documents:
        _open_documents[design] = ezf3d.readfile(design)
    return _open_documents[design]


@pytest.fixture(scope="session")
def _tessellation_cache():
    """Tessellations, computed once per sample.

    Triangulating every body of every sample is the other expensive thing this
    suite does, after parsing.  Several tests want the same result, so it is
    computed once and shared; tests only read.
    """
    return {}


@pytest.fixture
def tessellated(opened, sample: Path, _tessellation_cache):
    """Every body of the current sample, tessellated at the default tolerance."""
    from ezf3d.asm.brep import Shape
    from ezf3d.mesh import tessellate

    if sample not in _tessellation_cache:
        _tessellation_cache[sample] = [
            tessellate(Shape(body.model()), measure=False)
            for child in opened.documents()
            for body in child.bodies
        ]
    return _tessellation_cache[sample]


@pytest.fixture(scope="session")
def _face_mesh_cache():
    """Per-face tessellations, computed once per sample.

    Several tests want to look at every face's own mesh — that its triangles
    lie on its surface, that its outline does, that its area matches its
    loops.  Running ``tessellate_face`` over every face of every sample takes
    minutes, and doing it once per test took the suite from two and a half
    to twenty.  Tests only read.
    """
    return {}


@pytest.fixture
def meshed_faces(opened, sample: Path, _face_mesh_cache):
    """``(face, mesh, reason)`` for every face of the current sample."""
    from ezf3d.asm.brep import Shape
    from ezf3d.mesh import DEFAULT_CHORD_TOLERANCE, tessellate_face

    if sample not in _face_mesh_cache:
        _face_mesh_cache[sample] = [
            (face, *tessellate_face(face, DEFAULT_CHORD_TOLERANCE))
            for child in opened.documents()
            for body in child.bodies
            for face in Shape(body.model()).faces()
        ]
    return _face_mesh_cache[sample]


@pytest.fixture(scope="session")
def _design_cache():
    """The design graph and its parameters, read once per sample.

    Both walk the whole bulk stream — 6.4 MB in one of the package's members —
    and several tests want the same answer.  Tests only read.
    """
    return {}


@pytest.fixture
def parameter_sets(opened, sample: Path, _design_cache):
    """``(child document, Parameters)`` for every document with a design."""
    from ezf3d.model.parameters import read_parameters

    key = ("parameters", sample)
    if key not in _design_cache:
        _design_cache[key] = [
            (child, read_parameters(child.design)) for child in opened.documents() if child.design
        ]
    if not _design_cache[key]:
        pytest.skip("no design segment in this sample")
    return _design_cache[key]


@pytest.fixture
def read_design_cached(_design_cache):
    """:func:`~ezf3d.model.design.read_design`, memoised for the session."""
    from ezf3d.model.design import read_design

    def get(segment):
        key = ("design", id(segment))
        if key not in _design_cache:
            _design_cache[key] = read_design(segment)
        return _design_cache[key]

    return get
