# SPDX-License-Identifier: AGPL-3.0-or-later

"""The command line: argument parsing and the top-level run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pdf-images",
        description="Fetch a PDF, extract its images, and organize them by page.",
    )
    parser.add_argument(
        "pdf",
        metavar="PDF",
        help="the PDF to extract from, either a URL to fetch or a path to a local file",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path.cwd(),
        help="where the workspace directory is created (default: the current directory)",
    )
    parser.add_argument(
        "--refetch",
        action="store_true",
        help="re-download even when the workspace already holds a source.pdf",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="accept a download that does not look like a PDF",
    )

    junk = parser.add_argument_group(
        "junk filtering",
        "Rejects are quarantined under rejected/, never deleted. Set any threshold to 0 to disable that check.",
    )
    defaults = Thresholds()
    junk.add_argument("--no-filter", action="store_true", help="keep every image")
    junk.add_argument(
        "--min-dim", type=int, default=defaults.min_dim, help="reject images shorter than this on either side"
    )
    junk.add_argument(
        "--min-bytes", type=int, default=defaults.min_bytes, help="reject images whose payload is smaller than this"
    )
    junk.add_argument(
        "--max-aspect", type=float, default=defaults.max_aspect, help="reject images more elongated than this ratio"
    )
    junk.add_argument(
        "--ubiquitous-area",
        type=int,
        default=defaults.ubiquitous_area,
        help="an image smaller than this many pixels can be rejected as a repeated logo",
    )
    junk.add_argument(
        "--max-pages",
        type=int,
        default=defaults.max_pages,
        help="how many pages a small image may appear on before it counts as a logo"
        " (default: half the document, minimum 3)",
    )
    junk.add_argument("--keep-solid", action="store_true", help="keep near-uniform images")

    output = parser.add_argument_group("output")
    output.add_argument(
        "--dry-run",
        action="store_true",
        help="write nothing; print what would be extracted. The threshold tuning loop.",
    )
    output.add_argument("--pages", help="restrict which pages are written, e.g. 3-7,12")
    output.add_argument("--json", action="store_true", help="print the manifest instead of a table")
    return parser.parse_args(argv)


def thresholds_from(args: argparse.Namespace) -> Thresholds:
    if args.no_filter:
        return Thresholds(
            min_dim=0,
            min_bytes=0,
            max_aspect=0,
            hairline_dim=0,
            ubiquitous_area=0,
            keep_solid=True,
        )
    return Thresholds(
        min_dim=args.min_dim,
        min_bytes=args.min_bytes,
        max_aspect=args.max_aspect,
        ubiquitous_area=args.ubiquitous_area,
        max_pages=args.max_pages,
        keep_solid=args.keep_solid,
    )


def run(args: argparse.Namespace) -> int:
    source = resolve_source(args.pdf)

    if not source.is_url:
        local = Path(source.origin).expanduser()
        if not local.is_file():
            raise Failure(f"no such file: {local}")
        pdf_path = local
        workspace = workspace_for(source, args.outdir)
        if not args.dry_run:
            workspace.mkdir(parents=True, exist_ok=True)
    else:
        workspace = workspace_for(source, args.outdir)
        pdf_path = workspace / "source.pdf"
        # `source.pdf` is cached across runs so that retuning thresholds is cheap.
        if args.refetch or not (pdf_path.is_file() and pdf_path.stat().st_size > 0):
            source = fetch(source, pdf_path, force=args.force)
            workspace = workspace_for(source, args.outdir)
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
    thresholds = thresholds_from(args)
    selection = parse_page_selection(args.pages, document.page_count) if args.pages else None

    records = scan(document)
    apply_filters(records, thresholds, document.page_count)
    if not args.dry_run:
        write_images(records, workspace, document.page_count, selection)
    manifest = build_manifest(source, pdf_path, document, records, thresholds)

    if args.json:
        print(json.dumps(manifest, indent=2))
    elif args.dry_run:
        print_table(manifest)
    else:
        kept = sum(1 for record in records if record.kept)
        print(f"{pdf_path}: {document.page_count} pages, {kept} of {len(records)} images kept -> {workspace}")

    if not args.dry_run:
        (workspace / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


def main() -> int:
    try:
        return run(parse_args())
    except Failure as error:
        print(f"pdf-images: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
