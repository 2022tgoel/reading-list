from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from app.paper_extractor import PaperLink

log = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
S2_FIELDS = "title,authors,abstract,year,venue,externalIds,url"

_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


@dataclass
class PaperMetadata:
    title: str
    authors: str
    abstract: str
    url: str
    source: str
    year: int | None = None
    venue: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def resolve(link: PaperLink) -> PaperMetadata | None:
    """Resolve a PaperLink into full metadata, trying source-specific APIs first."""
    try:
        if link.source == "arxiv":
            return await _fetch_arxiv(link.paper_id, link.url)
        if link.source == "doi":
            return await _fetch_s2(f"DOI:{link.paper_id}", link.url, link.source)
        if link.source == "semantic_scholar":
            return await _fetch_s2(link.paper_id, link.url, link.source)
        if link.source == "acl":
            return await _fetch_s2(f"ACL:{link.paper_id}", link.url, link.source)
        if link.source in ("openreview", "neurips", "pmlr", "biorxiv", "nature", "jmlr", "aaai"):
            return await _fetch_s2_by_url(link.url, link.source)
        # For blogs, GitHub, PDFs, etc. -- fetch the page title
        return await _fetch_page_title(link.url, link.source)
    except Exception:
        log.exception("Failed to resolve metadata for %s", link.url)
        return None


async def resolve_url(url: str, source: str = "shared") -> PaperMetadata | None:
    """Resolve an arbitrary URL (used for multi-mention links)."""
    try:
        meta = await _fetch_s2_by_url(url, source)
        if meta and meta.title != url:
            return meta
        return await _fetch_page_title(url, source)
    except Exception:
        log.exception("Failed to resolve URL metadata for %s", url)
        return None


# ---------------------------------------------------------------------------
# arxiv
# ---------------------------------------------------------------------------

async def _fetch_arxiv(arxiv_id: str, url: str) -> PaperMetadata | None:
    clean_id = re.sub(r"v\d+$", "", arxiv_id)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(ARXIV_API, params={"id_list": clean_id, "max_results": "1"})
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    entry = root.find("atom:entry", _NS)
    if entry is None:
        return None

    title_el = entry.find("atom:title", _NS)
    summary_el = entry.find("atom:summary", _NS)
    published_el = entry.find("atom:published", _NS)

    title = " ".join((title_el.text or "").split()) if title_el is not None else ""
    abstract = " ".join((summary_el.text or "").split()) if summary_el is not None else ""

    authors = []
    for author_el in entry.findall("atom:author", _NS):
        name_el = author_el.find("atom:name", _NS)
        if name_el is not None and name_el.text:
            authors.append(name_el.text.strip())

    year = None
    if published_el is not None and published_el.text:
        try:
            year = int(published_el.text[:4])
        except (ValueError, IndexError):
            pass

    categories = []
    for cat_el in entry.findall("arxiv:primary_category", _NS):
        term = cat_el.get("term")
        if term:
            categories.append(term)

    return PaperMetadata(
        title=title,
        authors=", ".join(authors),
        abstract=abstract,
        url=url,
        source="arxiv",
        year=year,
        venue=categories[0] if categories else "arxiv",
    )


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

async def _fetch_s2(paper_id: str, fallback_url: str, source: str) -> PaperMetadata | None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{S2_API}/{paper_id}", params={"fields": S2_FIELDS})
        if resp.status_code == 404:
            return await _fetch_s2_by_url(fallback_url, source)
        resp.raise_for_status()
    return _parse_s2(resp.json(), fallback_url, source)


async def _fetch_s2_by_url(url: str, source: str) -> PaperMetadata | None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{S2_API}/URL:{url}", params={"fields": S2_FIELDS})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    return _parse_s2(resp.json(), url, source)


def _parse_s2(data: dict, fallback_url: str, source: str) -> PaperMetadata:
    title = data.get("title") or fallback_url
    authors = ", ".join(a.get("name", "") for a in (data.get("authors") or []))
    abstract = data.get("abstract") or ""
    year = data.get("year")
    venue = data.get("venue") or None
    url = data.get("url") or fallback_url
    return PaperMetadata(
        title=title, authors=authors, abstract=abstract,
        url=url, source=source, year=year, venue=venue,
    )


# ---------------------------------------------------------------------------
# Page title fallback (for blogs, GitHub, etc.)
# ---------------------------------------------------------------------------

class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


async def _fetch_page_title(url: str, source: str) -> PaperMetadata | None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "PaperBot/1.0"})
            resp.raise_for_status()
    except Exception:
        return PaperMetadata(title=url, authors="", abstract="", url=url, source=source)

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type:
        return PaperMetadata(title=url, authors="", abstract="", url=url, source=source)

    text = resp.text[:50_000]
    parser = _TitleParser()
    try:
        parser.feed(text)
    except Exception:
        pass

    title = " ".join(parser.title.split()).strip()
    if not title:
        title = url

    # Try to extract a meta description for the abstract
    desc = ""
    desc_match = re.search(
        r'<meta\s+(?:name|property)=["\'](?:description|og:description)["\']\s+content=["\']([^"\']+)',
        text, re.IGNORECASE,
    )
    if desc_match:
        desc = desc_match.group(1).strip()

    return PaperMetadata(
        title=title, authors="", abstract=desc, url=url, source=source,
    )
