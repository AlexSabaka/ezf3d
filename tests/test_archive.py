"""The Zstandard-aware ZIP reader."""

from __future__ import annotations

import io
import zipfile
import zlib

import pytest

from ezf3d.container.archive import ZIP_ZSTANDARD, F3DArchive


def test_stdlib_cannot_open_zstd_entries(design):
    """The reason this module exists: zipfile parses the directory but not the data."""
    with zipfile.ZipFile(design) as zf:
        zstd = [i for i in zf.infolist() if i.compress_type == ZIP_ZSTANDARD]
        assert zstd, "sample should exercise the zstd path"
        with pytest.raises(NotImplementedError):
            zf.read(zstd[0].filename)


def test_every_entry_round_trips_with_matching_crc(sample):
    with F3DArchive(sample) as archive:
        entries = list(archive.entries())
        assert entries
        for entry in entries:
            data = archive.read(entry.name)
            assert len(data) == entry.size
            if entry.crc:
                assert zlib.crc32(data) == entry.crc


def test_read_prefix_matches_full_read(design):
    with F3DArchive(design) as archive:
        for entry in archive.entries():
            if entry.size < 64:
                continue
            assert archive.read_prefix(entry.name, 64) == archive.read(entry.name)[:64]
            break


def test_reads_from_a_stream(design):
    """A .f3d nested in a .f3z is opened straight from memory."""
    with F3DArchive(io.BytesIO(design.read_bytes())) as archive:
        assert "Manifest.dat" in archive


def test_missing_entry_raises_key_error(design):
    with F3DArchive(design) as archive, pytest.raises(KeyError):
        archive.read("nope.dat")
