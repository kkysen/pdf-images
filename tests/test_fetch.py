# SPDX-License-Identifier: AGPL-3.0-or-later

"""URL parsing: slugs, and the `#page=N` fragment."""

import pytest

from pdf_images.fetch import page_from_fragment, resolve_source


@pytest.mark.parametrize(
    ("url", "page"),
    [
        ("https://example.org/a.pdf", None),
        ("https://example.org/a.pdf#page=22", 22),
        ("https://example.org/a.pdf#page=1", 1),
        # PDF Open Parameters may carry more than the page.
        ("https://example.org/a.pdf#page=7&zoom=150", 7),
        ("https://example.org/a.pdf#zoom=150&page=7", 7),
        # Anything unusable is ignored rather than rejected: a fragment is allowed
        # to hold things this tool has no use for.
        ("https://example.org/a.pdf#nameddest=intro", None),
        ("https://example.org/a.pdf#page=nonsense", None),
        ("https://example.org/a.pdf#page=0", None),
        ("https://example.org/a.pdf#page=-3", None),
    ],
)
def test_page_is_read_from_the_fragment(url: str, page: int | None) -> None:
    assert resolve_source(url).page == page


def test_the_fragment_is_not_part_of_the_request() -> None:
    """A fragment is a client-side instruction, never sent to the server.

    It must also not reach the slug, or the same document would land in a
    different workspace depending on which page was linked.
    """
    plain = resolve_source("https://example.org/paper.pdf")
    fragmented = resolve_source("https://example.org/paper.pdf#page=22")

    assert fragmented.request_url == plain.request_url == "https://example.org/paper.pdf"
    assert fragmented.slug == plain.slug == "paper"


def test_a_local_path_has_no_page() -> None:
    assert page_from_fragment("") is None
    assert resolve_source("tests/fixture.pdf").page is None
