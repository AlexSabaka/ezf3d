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
def opened_design(design: Path, _open_documents):
    """As :func:`opened`, but only for the plain ``.f3d`` samples."""
    if design not in _open_documents:
        _open_documents[design] = ezf3d.readfile(design)
    return _open_documents[design]
