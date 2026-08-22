# SPDX-License-Identifier: AGPL-3.0-or-later

"""Flags, reruns, and the failures that should exit nonzero rather than traceback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import Extract, ManifestOf

from pdf_images.cli import main, parse_args, run
from pdf_images.records import Failure


def test_dry_run_writes_nothing(fixture_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(parse_args([str(fixture_pdf), "--outdir", str(tmp_path), "--dry-run"])) == 0

    assert list(tmp_path.iterdir()) == []
    assert "ubiquitous" in capsys.readouterr().out


def test_json_output_is_valid(fixture_pdf: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(parse_args([str(fixture_pdf), "--outdir", str(tmp_path), "--dry-run", "--json"])) == 0

    manifest = json.loads(capsys.readouterr().out)
    assert manifest["page_count"] == 6
    assert len(manifest["images"]) == 7


def test_pages_restricts_output_but_not_verdicts(extract: Extract, manifest_of: ManifestOf) -> None:
    """Reuse counts stay whole-document, or `--pages 1` would call everything ubiquitous."""
    workspace = extract("--pages", "2,5")
    manifest = manifest_of(workspace)

    assert {path.name for path in (workspace / "pages").iterdir()} == {"p2", "p5"}
    logo = next(image for image in manifest["images"] if (image["width"], image["height"]) == (60, 60))
    assert logo["pages"] == [1, 2, 3, 4, 5, 6]
    assert logo["reason"] == "ubiquitous"


def test_rerun_is_idempotent(extract: Extract) -> None:
    def tree(workspace: Path) -> set[str]:
        return {str(path.relative_to(workspace)) for path in workspace.rglob("*.png")}

    assert tree(extract()) == tree(extract())


def test_retuning_leaves_no_stale_files(extract: Extract) -> None:
    """The extraction tree is rebuilt every run, so a threshold change is visible.

    Without this, files sorted under the previous run's thresholds would sit
    beside files sorted under the new ones.
    """
    workspace = extract()
    assert list((workspace / "pages").rglob("*.png"))

    workspace = extract("--min-dim", "500")
    assert not list((workspace / "pages").rglob("*.png"))
    assert list((workspace / "rejected").rglob("*.png"))


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["/nonexistent.pdf"], "no such file"),
        (["tests/fixture.pdf", "--pages", "9"], "outside this 6 page document"),
        (["tests/fixture.pdf", "--pages", "nonsense"], "cannot parse page selection"),
    ],
)
def test_expected_failures_are_reported(argv: list[str], expected: str, tmp_path: Path) -> None:
    with pytest.raises(Failure, match=expected):
        run(parse_args([*argv, "--outdir", str(tmp_path)]))


def test_main_exits_nonzero_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["pdf-images", "/nonexistent.pdf"])

    assert main() == 1
    assert capsys.readouterr().err.startswith("pdf-images: no such file")


@pytest.mark.network
def test_end_to_end_against_a_real_paper(tmp_path: Path) -> None:
    """Fetching, the `Content-Disposition` slug, and filtering on real content."""
    assert run(parse_args(["https://arxiv.org/pdf/1706.03762", "--outdir", str(tmp_path)])) == 0

    workspace = tmp_path / "1706-03762v7"
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["page_count"] == 15
    assert (workspace / "source.pdf").is_file()
    assert all(image["kept"] for image in manifest["images"])


@pytest.mark.network
def test_end_to_end_against_a_pdf_with_masked_images(tmp_path: Path) -> None:
    """Regression: every masked image here carries an opaque alpha channel of its own.

    Compositing rejects such a base, so this document aborted the whole run with
    a traceback. It also names itself via `Content-Disposition`, which is what
    left an empty directory behind under the URL basename.
    """
    assert run(parse_args(["https://www.mta.info/document/211811", "--outdir", str(tmp_path)])) == 0

    assert {path.name for path in tmp_path.iterdir()} == {"20260603-cb10-general-board-meeting"}
    manifest = json.loads((tmp_path / "20260603-cb10-general-board-meeting" / "manifest.json").read_text())
    assert not any(image["reason"] == "undecodable" for image in manifest["images"])
    assert sum(image["kept"] for image in manifest["images"]) == 24
