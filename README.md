# `pdf-images`

Fetch a PDF, extract its embedded images, organize them by page,
and quarantine the junk that PDFs routinely carry around.

## Why

`pdfimages` from poppler already extracts images well.
What it does not do is any of the surrounding work:

- it will not fetch a PDF from a URL,
- it will not create a workspace to keep the PDF and its output together,
- it dumps a flat pile of `prefix-NNN.png` into whatever directory you happen to be in,
  with the page number available only as an opt-in filename prefix and no grouping,
- and it makes no attempt to tell a real figure apart from
  the logos, hairline rules, and spacers that get embedded alongside it.

`pdf-images` is that scaffolding.
One argument in, a tidy per-page tree plus an auditable `manifest.json` out.

## Usage

```console
$ uv run pdf-images https://example.org/paper.pdf
$ uv run pdf-images ./local.pdf --dry-run
$ uv run pdf-images ./local.pdf --no-filter --outdir ~/scratch
```

`uv` resolves the dependencies and installs the `pdf-images` entry point on first run,
so there is no virtualenv to create or activate by hand.

The CLI is built with Typer, so `uv run pdf-images --help` groups the flags
into junk filtering and output panels.

## Development

```console
$ uv run ruff format          # and `ruff format --check` in CI
$ uv run ruff check
$ uv run ty check
$ uv run pyrefly check
$ uv run pytest
```

`.github/workflows/ci.yml` runs exactly those five.
`.pre-commit-config.yaml` runs the same set on commit;
`uv run pre-commit install` wires it up.

The end-to-end test fetches a paper from arXiv, so it is marked `network`
and deselected by default.
Run it with `uv run pytest -m network`.

Tests read the committed `tests/fixture.pdf`, a six page document
carrying one image per heuristic, and never regenerate it:
`tests/make_fixture.py` embeds a creation timestamp,
so a regenerated PDF differs byte for byte from the tracked one.
Regenerate it deliberately with `uv run python tests/make_fixture.py`.

### Layout

```
src/pdf_images/
  records.py    the shared error type and per-image record, importing nothing else
  fetch.py      URL or path -> a local PDF and a workspace
  extract.py    decoding each unique image once, and the per-page layout
  filters.py    the junk heuristics and their thresholds
  manifest.py   the run record, and the --dry-run table
  cli.py        the Typer command, and the `run` it delegates to
```

### Input

The `PDF` argument is fetched if it starts with `http://` or `https://`,
and treated as a path to a local file otherwise.

A URL may carry a PDF Open Parameters fragment naming a page,
as viewers and page-specific links produce:

```console
$ uv run pdf-images 'https://example.org/paper.pdf#page=22'
```

That extracts only page 22, exactly as `--pages 22` would,
and an explicit `--pages` wins if both are given.
Other fragment parameters (`#page=7&zoom=150`, `#nameddest=intro`) are ignored rather than rejected.
The fragment never reaches the server or the workspace name,
so the same document lands in the same directory whichever page was linked.
Quote the URL: `#` starts a comment in most shells.
Fetches follow redirects, check the `Content-Type`, and sniff for the `%PDF-` magic bytes;
`--force` skips the content-type check.

The workspace name is derived from, in order:
the `Content-Disposition` filename, the URL basename,
the document `Title` in the PDF metadata,
and finally a short hash of the URL.

### Output layout

```
<outdir>/<slug>/
  source.pdf          (fetched PDFs only)
  manifest.json
  pages/
    p001/p001-i01.png
    p001/p001-i02.png
    p003/p003-i01.jpg
  rejected/
    p001-i03.png
```

`--outdir` defaults to the current directory.
A local PDF is read where it sits and is not copied into the workspace,
so `source.pdf` appears only for a fetched one.
Page directories are zero padded to the width of the document's page count.

Images keep their original encoding whenever the PDF stores one directly,
so a JPEG in the PDF lands as a `.jpg` with its bytes untouched.
The single exception is an image with transparency:
a PDF stores the alpha channel separately, as a soft mask,
so those are composited back together and re-encoded as PNG.
Passing the original bytes through would silently flatten them.

Some producers emit an image carrying both a soft mask and its own alpha channel,
where that channel is an opaque placeholder rather than the mask.
The placeholder is stripped before compositing,
since keeping it would drop the transparency the mask describes.

There is deliberately no rule for rejecting masks.
A soft mask lives inside its parent image rather than in the page's resources,
so it never appears as a standalone image in the first place.

An image PyMuPDF cannot decode at all is quarantined as `undecodable` with a warning,
rather than ending the run: one bad image in a document should not cost the other twenty-five.

### Caching and reruns

`source.pdf` is cached: rerunning will not re-download it unless you pass `--refetch`.

The extraction tree is **not** cached.
`pages/` and `rejected/` are cleared and rebuilt on every run.
Without that, files sorted under a previous run's thresholds would linger
beside files sorted under the new ones, which is exactly what makes threshold tuning confusing.

## Deduplication

A PDF stores each image once and references it from every page that uses it.
`pdf-images` keys off that reference (the `xref`), so a header logo repeated on 200 pages
is decoded once, not 200 times.

The file is written under the first page that uses it and hard-linked into the others,
so browsing any single page directory still shows everything on that page
without duplicating bytes on disk.
The manifest records the full list of pages each image appears on.

## Junk filtering

Nothing is ever deleted.
Rejected images are moved to `rejected/`, and the manifest records *why* each one was rejected,
so you can see what a threshold actually did and retune it.

| Reason | Default rule | Flag |
|---|---|---|
| `too-small` | either dimension under 32 px | `--min-dim` |
| `too-few-bytes` | decoded payload under 512 bytes, a backstop checked last | `--min-bytes` |
| `hairline` | either side 3 px or less, or an aspect ratio worse than 50:1 | `--max-aspect` |
| `solid` | near-uniform color | `--keep-solid` |
| `ubiquitous` | small *and* reused across many pages: the logo/watermark signal | `--max-pages`, `--ubiquitous-area` |
| `undecodable` | PyMuPDF could not decode or composite it | none |

The order matters, and the payload floor runs *last*.
PNG compresses a solid 200x200 block to a few hundred bytes
and a real logo to under 2 KB,
so checking size first would swallow both `solid` and `ubiquitous`
and report the least informative of the three reasons.

`--no-filter` turns all of them off.
Individual checks can be disabled by setting their threshold to `0`.

### Why `ubiquitous` needs the size gate

Reuse count alone cannot separate a logo from a figure.
A document may legitimately repeat one large figure on every page,
and a small logo clears every other check here,
since a 60x60 logo beats the 32 px minimum dimension
and compresses to well above the payload floor.
Size is the only signal that actually discriminates the two,
so `ubiquitous` fires only when an image is *both* small (under 256x256 by default)
*and* present on at least half the pages.

Reuse counts are always computed over the whole document, never over a `--pages` selection.
Otherwise `--pages 1` would make every image appear on 1 of 1 pages, and so maximally ubiquitous.

### Known limitation

These are embedded XObjects, which is what the PDF actually contains,
not a rendering of what a page looks like.
A figure that the producing tool sliced into horizontal strips
arrives as a stack of strips rather than one image.
Reassembling those is not attempted.

## `manifest.json`

Per image: `xref`, the list of `pages` it appears on,
`placements` mapping each page to the image's position on it, `width`, `height`,
`colorspace`, `bpc`, `ext`, `bytes`, `sha256`, `kept`, `reason`, and the output path.
Plus a top-level block with the source, fetch timestamp, page count,
and every resolved threshold, so a run is reproducible and its filtering is auditable.

## Other flags

- `--dry-run` prints the manifest as a table and writes no images or `manifest.json`.
  This is the threshold tuning loop.
  A URL still has to be downloaded to be read, so a first `--dry-run` does cache `source.pdf`;
  subsequent runs reuse it.
- `--pages 3-7,12` restricts which pages get written.
- `--json` prints the manifest to stdout instead of the human-readable table.

The exit status is nonzero on a fetch failure, a PDF that will not open, or a document with no pages.

## License

AGPL-3.0-or-later, see `LICENSE`.

This is not a preference so much as an inheritance:
`pdf-images` imports PyMuPDF, which is dual licensed AGPL-3.0 or Artifex commercial,
and importing it makes this a derivative work.
Building the same tool on poppler's `pdfimages` invoked as a subprocess would avoid that,
since running a GPL binary at arm's length imposes nothing on the caller,
but PyMuPDF's per-page `xref` access is what makes the deduplication
and the soft-mask compositing here straightforward.
