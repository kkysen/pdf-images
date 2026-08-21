# SPDX-License-Identifier: AGPL-3.0-or-later

"""The shared vocabulary: the error type and the per-image record.

A leaf module importing nothing else in the package, so the modules that all
need these two names cannot form an import cycle around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class Failure(Exception):
    """An expected, reportable failure: printed without a traceback."""


@dataclass
class ImageRecord:
    """One unique image in the document, plus every page that references it."""

    xref: int
    # page number -> this image's position in that page's image list. The index
    # must be per page: the same image can sit at a different position on each
    # page it appears on, so a single stored index collides with another image's
    # filename as soon as the two are laid out on a page they share.
    placements: dict[int, int] = field(default_factory=dict)
    width: int = 0
    height: int = 0
    colorspace: str = ""
    bpc: int = 0
    ext: str = ""
    data: bytes = b""
    sha256: str = ""
    has_alpha: bool = False
    kept: bool = True
    reason: str | None = None
    path: str | None = None

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def pages(self) -> list[int]:
        return list(self.placements)

    @property
    def first_page(self) -> int:
        return next(iter(self.placements))

    def stem(self, page: int, width: int) -> str:
        return f"p{page:0{width}d}-i{self.placements[page] + 1:02d}"
