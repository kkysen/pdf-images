# SPDX-License-Identifier: AGPL-3.0-or-later

"""Layout, deduplication, and the transparency path."""

from __future__ import annotations

from conftest import Extract, ManifestOf
from PIL import Image


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

    The single-argument `pymupdf.Pixmap(document, xref)` returns the base image
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
