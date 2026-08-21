# SPDX-License-Identifier: AGPL-3.0-or-later

"""The junk heuristics, and the thresholds that drive them."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

from pdf_images.records import ImageRecord


@dataclass(frozen=True)
class Thresholds:
    """The junk heuristics, as data, so a run's filtering is reproducible.

    Every threshold is disabled by setting it to 0, and `--no-filter` zeroes
    them all. Rejected images are quarantined under `rejected/`, never deleted:
    a heuristic that guesses wrong should cost a second look, not the image.

    There is deliberately no soft-mask rule. `page.get_images()` lists only the
    images a page actually draws, and a soft mask lives inside its parent image's
    dict rather than in the page resources, so masks never reach us as standalone
    entries to reject. `_load` composites them into their parent instead.
    """

    min_dim: int = 32
    min_bytes: int = 512
    max_aspect: float = 50.0
    hairline_dim: int = 3
    ubiquitous_area: int = 256 * 256
    max_pages: int = 0  # 0 means "derive from the page count"
    keep_solid: bool = False
    solid_spread: int = 8

    def reuse_limit(self, page_count: int) -> int:
        """How many pages an image may appear on before it looks like furniture."""
        return self.max_pages or max(3, -(-page_count // 2))

    def as_dict(self, page_count: int) -> dict[str, object]:
        return {"reuse_limit": self.reuse_limit(page_count)} | {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


def is_near_solid(record: ImageRecord, spread: int) -> bool:
    """True when every channel varies by less than `spread` across the image.

    A flat rectangle is background or a spacer, never content worth keeping.
    """
    try:
        with Image.open(io.BytesIO(record.data)) as image:
            extrema = image.convert("RGB").getextrema()
    except Exception:
        # An image Pillow cannot decode is not evidence of anything; keep it.
        return False

    # Pillow returns one (low, high) pair per band for a multi-band image, but a
    # bare (low, high) for a single-band one. The convert("RGB") above rules the
    # latter out; this loop is written to satisfy the type checkers, which see
    # only the union of the two shapes.
    for band in extrema:
        if not isinstance(band, tuple):
            return False
        low, high = band
        if high - low >= spread:
            return False
    return True


def classify(record: ImageRecord, thresholds: Thresholds, page_count: int) -> str | None:
    """The reason this image is junk, or None to keep it.

    Ordered cheapest and most decisive first, so the recorded reason is the most
    informative one rather than whichever check happened to run.
    """
    smallest = min(record.width, record.height)
    largest = max(record.width, record.height)
    if thresholds.hairline_dim and smallest <= thresholds.hairline_dim:
        return "hairline"
    if thresholds.max_aspect and smallest and largest / smallest > thresholds.max_aspect:
        return "hairline"
    if thresholds.min_dim and smallest < thresholds.min_dim:
        return "too-small"

    # Size is the only signal separating a repeated logo from a repeated figure:
    # a document may legitimately carry one large figure on every page, and a
    # small logo clears every other check above. Hence both conditions.
    if (
        thresholds.ubiquitous_area
        and record.area < thresholds.ubiquitous_area
        and len(record.placements) >= thresholds.reuse_limit(page_count)
    ):
        return "ubiquitous"

    # A transparency-shaped image can be one flat color in RGB and still be
    # content, so the solid check only applies where there is no alpha channel.
    if not thresholds.keep_solid and not record.has_alpha and is_near_solid(record, thresholds.solid_spread):
        return "solid"

    # Last, and deliberately low. A payload floor is only a backstop for data too
    # degenerate for the checks above to describe: run it any earlier and it
    # swallows them, since PNG compresses a solid 200x200 block to a few hundred
    # bytes and a real logo to under 2 KB, both of which have better reasons.
    if thresholds.min_bytes and len(record.data) < thresholds.min_bytes:
        return "too-few-bytes"

    return None


def apply_filters(records: list[ImageRecord], thresholds: Thresholds, page_count: int) -> None:
    for record in records:
        record.reason = classify(record, thresholds, page_count)
        record.kept = record.reason is None
