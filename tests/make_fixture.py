#!/usr/bin/env -S uv run python
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Generate `tests/fixture.pdf`, a PDF shaped to exercise every junk heuristic.

The layout is deliberate. Each image below is the *only* one that should trip
its heuristic, and the two figures exist to prove the `ubiquitous` rule keys on
size rather than on reuse count: `figure-every` is reused on every page just
like `logo`, so a rule counting reuse alone would have to reject both or neither.
"""

import io
from pathlib import Path

import pymupdf
from PIL import Image

PAGES = 6


def png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def alpha_png(width: int, height: int) -> bytes:
    """An image with a real alpha gradient, stored by the PDF as a soft mask.

    Covers the one branch that re-encodes rather than passing bytes through.
    A single-argument `pymupdf.Pixmap(document, xref)` silently returns this
    without its alpha, so the extracted file's mode is what catches a regression.
    """
    image = Image.new("RGBA", (width, height))
    image.putdata(
        [
            ((x // 16 * 37) % 256, (y // 16 * 53) % 256, 120, x * 255 // width)
            for y in range(height)
            for x in range(width)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def patterned_png(width: int, height: int, seed: int) -> bytes:
    """A non-uniform image, so it is not caught by the `solid` heuristic.

    Coarse 16 px blocks rather than per-pixel noise: still plainly non-uniform,
    but it compresses, which keeps the committed fixture small.
    """
    image = Image.new("RGB", (width, height))
    image.putdata(
        [
            (
                (x // 16 * 37 + seed) % 256,
                (y // 16 * 53 + seed) % 256,
                ((x // 16) * (y // 16) * 29 + seed) % 256,
            )
            for y in range(height)
            for x in range(width)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# name -> (bytes, pages it appears on, expected verdict)
IMAGES = {
    "figure-every": (patterned_png(400, 300, 0), range(1, PAGES + 1), "kept"),
    "figure-sparse": (patterned_png(400, 300, 99), [2, 5], "kept"),
    "logo": (patterned_png(60, 60, 40), range(1, PAGES + 1), "ubiquitous"),
    "rule": (png(300, 2, (0, 0, 0)), range(1, PAGES + 1), "hairline"),
    "spacer": (png(16, 16, (255, 255, 255)), [1], "too-small"),
    "block": (png(200, 200, (255, 255, 255)), [3], "solid"),
    "translucent": (alpha_png(300, 300), [4], "kept, and still RGBA"),
}

PLACEMENTS = {
    "figure-every": pymupdf.Rect(50, 60, 250, 210),
    "figure-sparse": pymupdf.Rect(300, 60, 500, 210),
    "logo": pymupdf.Rect(10, 10, 40, 40),
    "rule": pymupdf.Rect(50, 230, 350, 232),
    "spacer": pymupdf.Rect(400, 10, 416, 26),
    "block": pymupdf.Rect(50, 260, 250, 460),
    "translucent": pymupdf.Rect(300, 260, 500, 460),
}


def main() -> None:
    document = pymupdf.open()
    for _ in range(PAGES):
        document.new_page()
    # Re-fetch each page by index rather than holding the `new_page()` return
    # values: `new_page()` invalidates previously returned `Page` objects.
    for name, (data, on_pages, _verdict) in IMAGES.items():
        for number in on_pages:
            document[number - 1].insert_image(PLACEMENTS[name], stream=data)

    out = Path(__file__).parent / "fixture.pdf"
    # `insert_image` stores the pixmaps uncompressed; deflate on save keeps the
    # committed fixture from being ~1 MB of raw RGB.
    document.save(out, deflate=True, deflate_images=True)
    print(f"wrote {out} ({PAGES} pages, {len(IMAGES)} unique images)")
    for name, (_data, on_pages, verdict) in IMAGES.items():
        print(f"  {name:<14} pages={list(on_pages)!s:<22} expect={verdict}")


if __name__ == "__main__":
    main()
