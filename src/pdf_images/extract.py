# SPDX-License-Identifier: AGPL-3.0-or-later

"""Decoding each unique image once and laying the results out by page."""

import os
import sys
from hashlib import sha256
from pathlib import Path
from shutil import copyfile, rmtree

from pymupdf import Document, Pixmap

from pdf_images.records import ImageRecord


def scan(document: Document) -> list[ImageRecord]:
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
        try:
            _load(document, record)
        except Exception as error:
            # PyMuPDF raises for image shapes it cannot decode or composite. One
            # such image should not cost the whole run, so it is quarantined with
            # the reason recorded, exactly like any other rejected image.
            print(f"warning: cannot decode image {record.xref}: {error}", file=sys.stderr)
            record.undecodable = True

    return list(records.values())


def _load(document: Document, record: ImageRecord) -> None:
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
        base = Pixmap(document, record.xref)
        # The base may already carry an alpha channel, and where it does that
        # channel is a fully opaque placeholder rather than the mask. Compositing
        # rejects a base with alpha outright, and keeping it would discard the
        # transparency, so strip it first and let the soft mask supply the real one.
        if base.alpha:
            base = Pixmap(base, 0)
        mask = Pixmap(document, raw["smask"])
        record.data = Pixmap(base, mask).tobytes("png")
        record.ext = "png"
        record.has_alpha = True
    else:
        record.data = raw["image"]
        # PyMuPDF reports "jpeg"; `.jpg` is what everything else writes.
        record.ext = "jpg" if raw["ext"] == "jpeg" else raw["ext"]

    record.sha256 = sha256(record.data).hexdigest()


def link_or_copy(source: Path, destination: Path) -> None:
    """Hard-link `source` to `destination`, falling back to a copy.

    An image reused across pages is written once and linked into the other page
    directories, so browsing a single page still shows everything on it without
    storing the bytes repeatedly.
    """
    try:
        os.link(source, destination)
    except OSError:
        copyfile(source, destination)


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
        rmtree(directory, ignore_errors=True)

    width = len(str(page_count))
    for record in records:
        if not record.kept:
            if record.undecodable:
                continue
            shown_on = sorted(selection.intersection(record.placements)) if selection is not None else record.pages
            if not shown_on:
                continue
            rejected_dir.mkdir(parents=True, exist_ok=True)
            # Named for the first page in the selection rather than in the document.
            # A logo on every page is a page 22 image too, and calling it `p01`
            # under `--pages 22` reads as a stray file from a page nobody asked for.
            target = rejected_dir / f"{record.stem(shown_on[0], width)}.{record.ext}"
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
