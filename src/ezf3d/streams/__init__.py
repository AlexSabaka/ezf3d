"""Decoders for Fusion's "Neutron" binary streams."""

from ezf3d.streams.manifest import (
    AssetManifest,
    DocumentManifest,
    SegmentDeclaration,
    read_asset_manifest,
    read_document_manifest,
)
from ezf3d.streams.primitives import FoundString, Reader, StreamError, scan_strings
from ezf3d.streams.segment import (
    BulkStream,
    MetaStream,
    Segment,
    read_bulk_stream,
    read_meta_stream,
    read_segment,
)

__all__ = [
    "AssetManifest",
    "BulkStream",
    "DocumentManifest",
    "FoundString",
    "MetaStream",
    "Reader",
    "Segment",
    "SegmentDeclaration",
    "StreamError",
    "read_asset_manifest",
    "read_bulk_stream",
    "read_document_manifest",
    "read_meta_stream",
    "read_segment",
    "scan_strings",
]
