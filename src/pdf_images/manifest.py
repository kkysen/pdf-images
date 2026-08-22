# SPDX-License-Identifier: AGPL-3.0-or-later

"""The run record: what was extracted, under which thresholds, and why.

`Manifest` is a `TypedDict` rather than a bare `dict` so the strict type checkers
can see through it, and so the JSON shape is stated in one place.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from pymupdf import Document

from pdf_images.fetch import Source
from pdf_images.filters import Thresholds
from pdf_images.records import ImageRecord


class ImageEntry(TypedDict):
    xref: int
    pages: list[int]
    placements: dict[int, int]
    width: int
    height: int
    colorspace: str
    bpc: int
    ext: str
    bytes: int
    sha256: str
    kept: bool
    reason: str | None
    path: str | None


class Manifest(TypedDict):
    source: str
    source_is_url: bool
    pdf: str
    extracted_at: str
    page_count: int
    thresholds: dict[str, object]
    images: list[ImageEntry]


def build_manifest(
    source: Source,
    pdf_path: Path,
    document: Document,
    records: list[ImageRecord],
    thresholds: Thresholds,
) -> Manifest:
    return {
        "source": source.origin,
        "source_is_url": source.is_url,
        "pdf": str(pdf_path),
        "extracted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "page_count": document.page_count,
        "thresholds": thresholds.as_dict(document.page_count),
        "images": [
            {
                "xref": record.xref,
                "pages": record.pages,
                "placements": record.placements,
                "width": record.width,
                "height": record.height,
                "colorspace": record.colorspace,
                "bpc": record.bpc,
                "ext": record.ext,
                "bytes": len(record.data),
                "sha256": record.sha256,
                "kept": record.kept,
                "reason": record.reason,
                "path": record.path,
            }
            for record in records
        ],
    }


def print_table(manifest: Manifest) -> None:
    """The `--dry-run` view: one row per image, with the verdict spelled out."""
    rows: list[tuple[str, ...]] = [("PAGES", "SIZE", "BYTES", "EXT", "VERDICT")]
    rows.extend(
        (
            ",".join(str(page) for page in image["pages"]),
            f"{image['width']}x{image['height']}",
            str(image["bytes"]),
            image["ext"],
            # `reason` is only None when the image was kept, but nothing in the
            # type says so, hence the fallback rather than an assert.
            "keep" if image["kept"] else image["reason"] or "rejected",
        )
        for image in manifest["images"]
    )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())

    kept = sum(1 for image in manifest["images"] if image["kept"])
    print(f"\n{kept} of {len(manifest['images'])} images kept over {manifest['page_count']} pages")
