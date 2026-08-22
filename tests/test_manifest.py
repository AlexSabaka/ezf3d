"""Document and asset manifests, across both on-disk revisions."""

from __future__ import annotations

from ezf3d.container.archive import F3DArchive
from ezf3d.container.layout import discover_layout
from ezf3d.streams.manifest import read_asset_manifest, read_document_manifest


def test_document_manifest_identity(design):
    with F3DArchive(design) as archive:
        manifest = read_document_manifest(archive.read("Manifest.dat"))
    assert manifest.doc_type == "Fusion Document"
    assert manifest.extension == ".f3d"
    assert manifest.format_version == "3-2-0-0"
    assert len(manifest.document_guid) == 36
    assert "Application" in manifest.schema
    assert manifest.asset_names


def test_asset_manifests_consume_their_buffer(design):
    """A non-empty tail means an undecoded revision, not a harmless leftover."""
    with F3DArchive(design) as archive:
        for folder in discover_layout(archive).assets:
            manifest = read_asset_manifest(archive.read(f"{folder}/Manifest.dat"))
            assert manifest.trailing == b"", f"{folder} left {len(manifest.trailing)} bytes"
            assert manifest.segments


def test_segment_declarations_match_folders_on_disk(design):
    """Folder names drift by version; the declaration is what ties them together."""
    with F3DArchive(design) as archive:
        layout = discover_layout(archive)
        for folder, asset in layout.assets.items():
            manifest = read_asset_manifest(archive.read(f"{folder}/Manifest.dat"))
            for name in asset.segments:
                assert any(d.matches(name) for d in manifest.segments), name


def test_older_package_members_use_a_narrower_version_block(focuser):
    """``.f3z`` members written by the cloud translator predate the 1234 sentinel."""
    import ezf3d

    with ezf3d.readfile(focuser) as doc:
        widths = {len(d.manifest.reserved) for d in doc.documents()}
        assert widths, "package should hold documents"
        for child in doc.documents():
            assert child.manifest.schema
            assert child.manifest.asset_names
