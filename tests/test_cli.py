# SPDX-License-Identifier: AGPL-3.0-or-later

"""Flags, reruns, and the failures that should exit nonzero rather than traceback."""

import json
from pathlib import Path

import pytest
from conftest import Extract, ManifestOf
from typer.testing import CliRunner

from pdf_images.cli import app


def test_dry_run_writes_nothing(fixture_pdf: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [str(fixture_pdf), "--outdir", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert list(tmp_path.iterdir()) == []
    assert "ubiquitous" in result.output


def test_json_output_is_valid(fixture_pdf: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [str(fixture_pdf), "--outdir", str(tmp_path), "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    manifest = json.loads(result.output)
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


def test_a_page_selection_reports_what_was_written(fixture_pdf: Path, tmp_path: Path) -> None:
    """The document-wide count alone makes an almost empty workspace look wrong."""
    result = CliRunner().invoke(app, [str(fixture_pdf), "--outdir", str(tmp_path), "--pages", "4"])

    assert result.exit_code == 0, result.output
    assert "3 of 7 images kept" in result.output
    assert "wrote 2 on page 4" in result.output


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
def test_expected_failures_exit_nonzero_without_a_traceback(argv: list[str], expected: str, tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [*argv, "--outdir", str(tmp_path)])

    assert result.exit_code == 1
    assert expected in result.output
    assert "Traceback" not in result.output


@pytest.mark.network
def test_end_to_end_against_a_real_paper(tmp_path: Path) -> None:
    """Fetching, the `Content-Disposition` slug, and filtering on real content."""
    result = CliRunner().invoke(app, ["https://arxiv.org/pdf/1706.03762", "--outdir", str(tmp_path)])
    assert result.exit_code == 0, result.output

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
    result = CliRunner().invoke(app, ["https://www.mta.info/document/211811", "--outdir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    assert {path.name for path in tmp_path.iterdir()} == {"20260603-cb10-general-board-meeting"}
    manifest = json.loads((tmp_path / "20260603-cb10-general-board-meeting" / "manifest.json").read_text())
    assert not any(image["reason"] == "undecodable" for image in manifest["images"])
    assert sum(image["kept"] for image in manifest["images"]) == 24


@pytest.mark.network
def test_a_page_fragment_restricts_the_output(tmp_path: Path) -> None:
    """`#page=N` should behave exactly as `--pages N` does."""
    result = CliRunner().invoke(app, ["https://www.mta.info/document/211811#page=22", "--outdir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    workspace = tmp_path / "20260603-cb10-general-board-meeting"
    assert {path.name for path in (workspace / "pages").iterdir()} == {"p22"}
    # The fragment restricts what is written, not which verdicts are reached:
    # the manifest still describes every image in the document.
    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["page_count"] == 27
    assert len(manifest["images"]) == 26


@pytest.mark.network
def test_an_explicit_pages_option_beats_the_fragment(tmp_path: Path) -> None:
    args = ["https://www.mta.info/document/211811#page=22", "--pages", "21", "--outdir", str(tmp_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output

    workspace = tmp_path / "20260603-cb10-general-board-meeting"
    assert {path.name for path in (workspace / "pages").iterdir()} == {"p21"}
