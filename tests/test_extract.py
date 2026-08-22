# SPDX-License-Identifier: AGPL-3.0-or-later

"""Layout, deduplication, and the transparency path."""

from pathlib import Path

import pymupdf
import pytest
from conftest import Extract, ManifestOf
from PIL import Image
from pymupdf import Pixmap


def test_every_placement_gets_its_own_file(extract: Extract, manifest_of: ManifestOf) -> None:
    """A regression guard for filename collisions between images sharing a page.

    Filenames carry a per-page index. Using one index per image instead, taken
    from its first page, silently overwrites a file whenever two images appear
    together on a later page in a different order.
    """
    workspace = extract("--no-filter")
    manifest = manifest_of(workspace)

    placements = sum(len(image["pages"]) for image in manifest["images"])
    written = list((workspace / "pages").rglob("*.*"))
    assert len(written) == placements
    assert len({path.name for path in written}) == len(written)


def test_reused_images_share_one_inode(extract: Extract, manifest_of: ManifestOf) -> None:
    workspace = extract()
    manifest = manifest_of(workspace)
    reused = next(image for image in manifest["images"] if image["kept"] and len(image["pages"]) == 6)

    # JSON object keys are strings, so the page numbers come back as text.
    width = len(str(manifest["page_count"]))
    inodes = {
        (workspace / "pages" / f"p{int(page):0{width}d}" / f"p{int(page):0{width}d}-i{index + 1:02d}.png").stat().st_ino
        for page, index in reused["placements"].items()
    }
    assert len(inodes) == 1, "a reused image should be stored once and hard-linked"


def test_transparency_survives_extraction(extract: Extract, manifest_of: ManifestOf) -> None:
    """A PDF stores alpha as a separate soft mask, which must be composited back.

    The single-argument `Pixmap(document, xref)` returns the base image
    with no alpha at all, so this asserts on the mode of the extracted file
    rather than merely on the extraction succeeding.
    """
    workspace = extract()
    manifest = manifest_of(workspace)
    translucent = next(image for image in manifest["images"] if (image["width"], image["height"]) == (300, 300))

    assert translucent["kept"]
    path = translucent["path"]
    assert path is not None
    with Image.open(workspace / path) as image:
        assert image.mode == "RGBA"
        # getchannel returns a single band, whose extrema is a plain (low, high).
        low, _high = image.getchannel("A").getextrema()
        assert low == 0, "the fully transparent edge should still be transparent"


def test_rejects_are_quarantined_not_deleted(extract: Extract, manifest_of: ManifestOf) -> None:
    workspace = extract()
    manifest = manifest_of(workspace)
    rejected = [image for image in manifest["images"] if not image["kept"]]

    assert rejected
    for image in rejected:
        path = image["path"]
        assert path is not None
        assert (workspace / path).is_file()
        assert path.startswith("rejected/")


def test_a_base_with_alpha_cannot_be_composited_directly(fixture_pdf: Path) -> None:
    """Pins the PyMuPDF behaviour that `_load`'s alpha guard exists for.

    Some producers emit an image that carries both a soft mask and its own alpha
    channel, where that channel is an opaque placeholder rather than the mask.
    Compositing rejects such a base outright, which is the crash this guards, and
    keeping its alpha instead of the mask's would silently drop the transparency.
    PyMuPDF's own writer cannot produce that shape, so the contract is asserted
    here directly rather than through a fixture PDF.
    """
    document = pymupdf.open(fixture_pdf)
    translucent = next(
        entry for page in range(document.page_count) for entry in document[page].get_images(full=True) if entry[1]
    )
    base = Pixmap(document, translucent[0])
    mask = Pixmap(document, translucent[1])

    with_alpha = Pixmap(base, 1)
    assert with_alpha.alpha, "the setup itself must produce the shape being guarded against"
    with pytest.raises(Exception, match="must not have an alpha channel"):
        Pixmap(with_alpha, mask)

    stripped = Pixmap(with_alpha, 0)
    assert not stripped.alpha
    assert Pixmap(stripped, mask).alpha, "stripping first is what makes compositing work"
