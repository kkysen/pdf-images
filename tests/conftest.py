# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared fixtures.

Everything reads the committed `tests/fixture.pdf` rather than regenerating it.
`make_fixture.py` embeds a creation timestamp, so a regenerated PDF differs byte
for byte from the tracked one, and a test that rewrote it would dirty the tree.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from pdf_images.cli import parse_args, run
from pdf_images.manifest import Manifest

FIXTURE = Path(__file__).parent / "fixture.pdf"

# The two callables the fixtures below hand to tests.
Extract = Callable[..., Path]
ManifestOf = Callable[[Path], Manifest]


@pytest.fixture
def fixture_pdf() -> Path:
    return FIXTURE


@pytest.fixture
def extract(tmp_path: Path) -> Extract:
    """Run the CLI against the fixture and return the workspace directory."""

    def _extract(*flags: str, pdf: str = str(FIXTURE)) -> Path:
        assert run(parse_args([pdf, "--outdir", str(tmp_path), *flags])) == 0
        return tmp_path / "fixture"

    return _extract


@pytest.fixture
def manifest_of() -> ManifestOf:
    def _manifest_of(workspace: Path) -> Manifest:
        # Note that JSON object keys are strings, so `placements` comes back
        # keyed by text rather than by the int page numbers that were written.
        manifest: Manifest = json.loads((workspace / "manifest.json").read_text())
        return manifest

    return _manifest_of
