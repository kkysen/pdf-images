# SPDX-License-Identifier: AGPL-3.0-or-later

"""Decoding each unique image once and laying the results out by page."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pymupdf

from pdf_images.records import ImageRecord


def scan(document: pymupdf.Document) -> list[ImageRecord]:
    """Collect every unique image, keyed by `xref`, in first-appearance order.

    A PDF stores each image once and references it from every page that uses it,
    so keying on `xref` both deduplicates the extraction work and hands us the
    cross-page reuse count that the `ubiquitous` heuristic needs. Reuse is always
    counted over the whole document, never over a `--pages` selection, or
    restricting to one page would make every image on it maximally ubiquitous.
    """
    records: dict[int, ImageRecord] = {}

    # Indexed rather than iterated: `Document` exposes `__getitem__` but is not
    # typed as an iterable, so `enumerate(document)` does not type check.
    for number in range(1, document.page_count + 1):
        for index, entry in enumerate(document[number - 1].get_images(full=True)):
            xref = entry[0]
            record = records.get(xref)
            if record is None:
                record = records[xref] = ImageRecord(xref=xref)
            record.placements[number] = index

    for record in records.values():
        _load(document, record)

    return list(records.values())


def _load(document: pymupdf.Document, record: ImageRecord) -> None:
    """Decode one image, recomposing with its soft mask when it has one."""
    raw = document.extract_image(record.xref)
    record.width = raw["width"]
    record.height = raw["height"]
    record.colorspace = raw.get("cs-name", "") or str(raw.get("colorspace", ""))
    record.bpc = raw["bpc"]

    if raw.get("smask"):
        # Keeping the original bytes would silently drop transparency, so this is
        # the one case where re-encoding (always as PNG) is worth the loss of fidelity.
        #
        # The two-argument Pixmap is what actually composites. The single-argument
        # `Pixmap(document, xref)` returns the base image with no alpha at all,
        # which would strip exactly the transparency this branch exists to keep.
        base = pymupdf.Pixmap(document, record.xref)
        mask = pymupdf.Pixmap(document, raw["smask"])
        record.data = pymupdf.Pixmap(base, mask).tobytes("png")
        record.ext = "png"
        record.has_alpha = True
    else:
        record.data = raw["image"]
        record.ext = raw["ext"]

    record.sha256 = hashlib.sha256(record.data).hexdigest()


def link_or_copy(source: Path, destination: Path) -> None:
    """Hard-link `source` to `destination`, falling back to a copy.

    An image reused across pages is written once and linked into the other page
    directories, so browsing a single page still shows everything on it without
    storing the bytes repeatedly.
    """
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def write_images(
    records: list[ImageRecord],
    workspace: Path,
    page_count: int,
    selection: set[int] | None = None,
) -> None:
    """Lay the extracted images out under `pages/`, with rejects in `rejected/`."""
    pages_dir = workspace / "pages"
    rejected_dir = workspace / "rejected"
    # The tree is rebuilt every run: leaving files sorted under a previous run's
    # thresholds beside files sorted under the current ones makes tuning useless.
    for directory in (pages_dir, rejected_dir):
        shutil.rmtree(directory, ignore_errors=True)

    width = len(str(page_count))
    for record in records:
        if not record.kept:
            if selection is not None and not selection.intersection(record.placements):
                continue
            rejected_dir.mkdir(parents=True, exist_ok=True)
            target = rejected_dir / f"{record.stem(record.first_page, width)}.{record.ext}"
            target.write_bytes(record.data)
            record.path = str(target.relative_to(workspace))
            continue

        first: Path | None = None
        # `--pages` restricts only what is written. Reuse counts, and therefore
        # the filtering verdicts, were already computed over the whole document.
        for page in record.pages:
            if selection is not None and page not in selection:
                continue
            page_dir = pages_dir / f"p{page:0{width}d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            target = page_dir / f"{record.stem(page, width)}.{record.ext}"
            if first is None:
                target.write_bytes(record.data)
                first = target
                record.path = str(target.relative_to(workspace))
            else:
                target.unlink(missing_ok=True)
                link_or_copy(first, target)
