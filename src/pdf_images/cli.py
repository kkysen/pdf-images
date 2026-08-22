# SPDX-License-Identifier: AGPL-3.0-or-later

"""The command line: argument parsing and the top-level run."""

import json
import sys
from pathlib import Path
from typing import Annotated

from typer import Argument, Exit, Option, Typer

from pdf_images.extract import scan, write_images
from pdf_images.fetch import fetch, open_document, resolve_source, workspace_for
from pdf_images.filters import Thresholds, apply_filters
from pdf_images.manifest import build_manifest, print_table
from pdf_images.records import Failure


def parse_page_selection(spec: str, page_count: int) -> set[int]:
    """Parse `3-7,12` into a set of page numbers, validated against the document."""
    selected: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part.lstrip("-"):
                start, _, end = part.partition("-")
                selected.update(range(int(start), int(end) + 1))
            else:
                selected.add(int(part))
        except ValueError:
            raise Failure(f"cannot parse page selection {part!r}") from None

    out_of_range = sorted(n for n in selected if not 1 <= n <= page_count)
    if out_of_range:
        raise Failure(f"pages {out_of_range} are outside this {page_count} page document")
    if not selected:
        raise Failure(f"page selection {spec!r} selects nothing")
    return selected


def run(
    pdf: str,
    outdir: Path,
    thresholds: Thresholds,
    *,
    refetch: bool = False,
    force: bool = False,
    dry_run: bool = False,
    pages: str | None = None,
    as_json: bool = False,
) -> int:
    """Do the whole job. Separated from the Typer command so tests can call it directly."""
    source = resolve_source(pdf)

    if not source.is_url:
        local = Path(source.origin).expanduser()
        if not local.is_file():
            raise Failure(f"no such file: {local}")
        pdf_path = local
        workspace = workspace_for(source, outdir)
        if not dry_run:
            workspace.mkdir(parents=True, exist_ok=True)
    else:
        workspace = workspace_for(source, outdir)
        pdf_path = workspace / "source.pdf"
        # `source.pdf` is cached across runs so that retuning thresholds is cheap.
        if refetch or not (pdf_path.is_file() and pdf_path.stat().st_size > 0):
            source = fetch(source, pdf_path, force=force)
            workspace = workspace_for(source, outdir)
            resolved = workspace / "source.pdf"
            if resolved != pdf_path:
                # The response told us a better name than the URL did.
                resolved.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.replace(resolved)
                # The URL basename already created a directory. Now that a better
                # name has won, leave no empty one behind.
                pdf_path.parent.rmdir()
                pdf_path = resolved

    document = open_document(pdf_path)
    selection = parse_page_selection(pages, document.page_count) if pages else None

    records = scan(document)
    apply_filters(records, thresholds, document.page_count)
    if not dry_run:
        write_images(records, workspace, document.page_count, selection)
    manifest = build_manifest(source, pdf_path, document, records, thresholds)

    if as_json:
        print(json.dumps(manifest, indent=2))
    elif dry_run:
        print_table(manifest)
    else:
        kept = sum(1 for record in records if record.kept)
        summary = f"{document.page_count} pages, {kept} of {len(records)} images kept"
        if selection is not None:
            # With a page selection the document-wide count is not what was written,
            # and saying only the former makes an almost empty workspace look wrong.
            written = sum(len(selection.intersection(r.placements)) for r in records if r.kept)
            pages_written = ",".join(str(page) for page in sorted(selection))
            summary += f"; wrote {written} on page {pages_written}"
        print(f"{pdf_path}: {summary} -> {workspace}")

    if not dry_run:
        (workspace / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


app = Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Fetch a PDF, extract its images, and organize them by page.",
)

_DEFAULTS = Thresholds()

JUNK = "junk filtering"
OUTPUT = "output"


@app.command()
def extract(
    pdf: Annotated[
        str,
        Argument(metavar="PDF", help="the PDF to extract from, either a URL to fetch or a path to a local file"),
    ],
    outdir: Annotated[
        Path, Option(help="where the workspace directory is created", show_default="the current directory")
    ] = Path("."),
    refetch: Annotated[
        bool, Option("--refetch", help="re-download even when the workspace already holds a source.pdf")
    ] = False,
    force: Annotated[bool, Option("--force", help="accept a download that does not look like a PDF")] = False,
    no_filter: Annotated[bool, Option("--no-filter", help="keep every image", rich_help_panel=JUNK)] = False,
    min_dim: Annotated[
        int, Option(help="reject images shorter than this on either side", rich_help_panel=JUNK)
    ] = _DEFAULTS.min_dim,
    min_bytes: Annotated[
        int, Option(help="reject images whose payload is smaller than this", rich_help_panel=JUNK)
    ] = _DEFAULTS.min_bytes,
    max_aspect: Annotated[
        float, Option(help="reject images more elongated than this ratio", rich_help_panel=JUNK)
    ] = _DEFAULTS.max_aspect,
    ubiquitous_area: Annotated[
        int,
        Option(help="an image smaller than this many pixels can be rejected as a repeated logo", rich_help_panel=JUNK),
    ] = _DEFAULTS.ubiquitous_area,
    max_pages: Annotated[
        int,
        Option(
            help="how many pages a small image may appear on before it counts as a logo",
            show_default="half the document, minimum 3",
            rich_help_panel=JUNK,
        ),
    ] = _DEFAULTS.max_pages,
    keep_solid: Annotated[bool, Option("--keep-solid", help="keep near-uniform images", rich_help_panel=JUNK)] = False,
    dry_run: Annotated[
        bool,
        Option(
            "--dry-run",
            help="write nothing; print what would be extracted. The threshold tuning loop.",
            rich_help_panel=OUTPUT,
        ),
    ] = False,
    pages: Annotated[
        str | None, Option(help="restrict which pages are written, e.g. 3-7,12", rich_help_panel=OUTPUT)
    ] = None,
    as_json: Annotated[
        bool, Option("--json", help="print the manifest instead of a table", rich_help_panel=OUTPUT)
    ] = False,
) -> None:
    """Fetch a PDF, extract its images, and organize them by page.

    Rejects are quarantined under rejected/, never deleted.
    Set any junk filtering threshold to 0 to disable that check.
    """
    thresholds = (
        Thresholds(min_dim=0, min_bytes=0, max_aspect=0, hairline_dim=0, ubiquitous_area=0, keep_solid=True)
        if no_filter
        else Thresholds(
            min_dim=min_dim,
            min_bytes=min_bytes,
            max_aspect=max_aspect,
            ubiquitous_area=ubiquitous_area,
            max_pages=max_pages,
            keep_solid=keep_solid,
        )
    )
    try:
        run(
            pdf,
            outdir,
            thresholds,
            refetch=refetch,
            force=force,
            dry_run=dry_run,
            pages=pages,
            as_json=as_json,
        )
    except Failure as error:
        # An expected failure is a message and a nonzero exit, never a traceback.
        print(f"pdf-images: {error}", file=sys.stderr)
        raise Exit(1) from error


if __name__ == "__main__":
    app()
