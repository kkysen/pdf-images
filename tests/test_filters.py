# SPDX-License-Identifier: AGPL-3.0-or-later

"""Each fixture image must draw its own intended rejection reason.

The reasons matter as much as the keep/reject split: a heuristic that rejects
the right image for the wrong reason cannot be retuned from the manifest, which
is the whole point of recording it.
"""

from __future__ import annotations

from conftest import Extract, ManifestOf

from pdf_images.manifest import Manifest

# (width, height) -> the verdict that image exists to produce.
EXPECTED = {
    (400, 300): None,  # both figures, one reused on every page and one on two
    (60, 60): "ubiquitous",
    (300, 2): "hairline",
    (16, 16): "too-small",
    (200, 200): "solid",
    (300, 300): None,  # the translucent image
}


def verdicts(manifest: Manifest) -> list[tuple[tuple[int, int], str | None]]:
    return [((image["width"], image["height"]), image["reason"]) for image in manifest["images"]]


def test_each_image_gets_its_intended_reason(extract: Extract, manifest_of: ManifestOf) -> None:
    manifest = manifest_of(extract())
    for size, reason in verdicts(manifest):
        assert reason == EXPECTED[size], f"{size} was rejected as {reason}"


def test_a_large_image_reused_on_every_page_is_not_ubiquitous(extract: Extract, manifest_of: ManifestOf) -> None:
    """The size gate, which is the only thing separating a logo from a figure.

    `figure-every` and `logo` both appear on all six pages, so a rule counting
    reuse alone would have to reject both.
    """
    manifest = manifest_of(extract())
    figure = next(i for i in manifest["images"] if (i["width"], i["height"]) == (400, 300) and len(i["pages"]) == 6)
    logo = next(i for i in manifest["images"] if (i["width"], i["height"]) == (60, 60))

    assert len(figure["pages"]) == len(logo["pages"]) == 6
    assert figure["kept"]
    assert logo["reason"] == "ubiquitous"


def test_no_filter_keeps_everything(extract: Extract, manifest_of: ManifestOf) -> None:
    workspace = extract("--no-filter")
    manifest = manifest_of(workspace)

    assert all(image["kept"] for image in manifest["images"])
    assert not (workspace / "rejected").exists()
