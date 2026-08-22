# SPDX-License-Identifier: AGPL-3.0-or-later

"""Turning a URL or a path into a local PDF and a workspace to put it beside."""

import re
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import pymupdf
from httpx import HTTPError
from pymupdf import Document

from pdf_images.records import Failure

PDF_MAGIC = b"%PDF-"
PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/x-pdf", "application/octet-stream"})


def slugify(text: str) -> str:
    """Reduce arbitrary text to a filesystem-safe `[a-z0-9-]` name."""
    text = re.sub(r"\.pdf$", "", text.strip(), flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80]


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    # `filename*=UTF-8''name.pdf` takes precedence over plain `filename="name.pdf"`.
    extended = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", header, flags=re.IGNORECASE)
    if extended:
        return unquote(extended.group(1).strip())
    plain = re.search(r'filename\s*=\s*"?([^";]+)"?', header, flags=re.IGNORECASE)
    return plain.group(1).strip() if plain else None


@dataclass(frozen=True)
class Source:
    """Where the PDF came from, and what to call its workspace."""

    origin: str
    is_url: bool
    slug: str


def resolve_source(pdf: str) -> Source:
    """Decide the workspace slug from a PDF URL or path, before anything is fetched."""
    is_url = pdf.startswith(("http://", "https://"))
    if not is_url:
        return Source(origin=pdf, is_url=False, slug=slugify(Path(pdf).stem) or "document")

    basename = Path(unquote(urlparse(pdf).path)).name
    return Source(origin=pdf, is_url=True, slug=slugify(basename))


def fetch(source: Source, destination: Path, *, force: bool) -> Source:
    """Download to `destination`, refining the slug from the response if we can.

    The slug may change here: a `Content-Disposition` filename beats the URL
    basename, and a URL with no usable basename has no slug at all until now.
    """
    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        try:
            response = client.get(source.origin)
            response.raise_for_status()
        except HTTPError as error:
            raise Failure(f"fetching {source.origin}: {error}") from error

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    body = response.content
    if not body.startswith(PDF_MAGIC):
        if not force:
            raise Failure(
                f"{source.origin} is not a PDF"
                f" (content-type {content_type or 'unset'},"
                f" starts with {body[:16]!r}); pass --force to try anyway"
            )
    elif content_type and content_type not in PDF_CONTENT_TYPES:
        # The bytes are a PDF, so the server just mislabeled it. Note it and continue.
        print(f"warning: {source.origin} served as {content_type}, but the bytes are a PDF", file=sys.stderr)

    slug = (
        slugify(filename_from_content_disposition(response.headers.get("content-disposition")) or "")
        or source.slug
        or f"pdf-{sha256(source.origin.encode()).hexdigest()[:12]}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return Source(origin=source.origin, is_url=True, slug=slug)


def open_document(path: Path) -> Document:
    try:
        document = pymupdf.open(path)
    except Exception as error:  # pymupdf raises a variety of types here
        raise Failure(f"opening {path}: {error}") from error
    if document.needs_pass:
        raise Failure(f"{path} is encrypted and no password was supplied")
    if document.page_count == 0:
        raise Failure(f"{path} has no pages")
    return document


def workspace_for(source: Source, outdir: Path) -> Path:
    """The `<outdir>/<slug>/` directory holding the PDF and everything derived from it."""
    return outdir / (source.slug or "document")
