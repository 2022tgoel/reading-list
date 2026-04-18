from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class PaperLink:
    url: str
    source: str
    paper_id: str  # normalized identifier or canonical URL


# ---------------------------------------------------------------------------
# Academic paper patterns (these get full metadata resolution via APIs)
# ---------------------------------------------------------------------------

_ARXIV_ID = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE
)
_ARXIV_OLD = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([a-z\-]+/\d{7}(?:v\d+)?)", re.IGNORECASE
)
_DOI = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s,;\"'<>]+)", re.IGNORECASE)
_OPENREVIEW = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", re.IGNORECASE)
_SEMANTIC_SCHOLAR = re.compile(
    r"semanticscholar\.org/paper/[^/]*/([a-f0-9]{40})", re.IGNORECASE
)
_ACL_ANTHOLOGY = re.compile(r"aclanthology\.org/([A-Z0-9\-]+\.?\d*)", re.IGNORECASE)
_NEURIPS = re.compile(
    r"papers\.nips\.cc/paper(?:_files)?/(\d{4})/(?:hash|file)/([a-f0-9]+)", re.IGNORECASE
)
_PMLR = re.compile(r"proceedings\.mlr\.press/(v\d+/[a-z0-9\-]+)", re.IGNORECASE)
_BIORXIV = re.compile(r"(?:bio|med)rxiv\.org/content/(10\.\d+/[\d.]+)", re.IGNORECASE)
_NATURE = re.compile(r"nature\.com/articles/([\w\-]+)", re.IGNORECASE)
_SCIENCE = re.compile(r"science\.org/doi/(10\.\d+/[^\s,;\"'<>]+)", re.IGNORECASE)
_JMLR = re.compile(r"jmlr\.org/papers/(v\d+/[\w\-]+)", re.IGNORECASE)
_AAAI = re.compile(r"ojs\.aaai\.org/index\.php/\w+/article/view/(\d+)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Domains to SKIP -- noise that is never useful as a reading list item.
# Everything NOT in this list is considered potentially interesting content.
# ---------------------------------------------------------------------------

_SKIP_DOMAINS = {
    # Social media
    "twitter.com", "x.com", "t.co",
    "instagram.com", "facebook.com", "fb.com",
    "linkedin.com",
    "reddit.com", "old.reddit.com",
    "threads.net",
    "mastodon.social",
    "bsky.app",
    # Image / media hosting
    "pbs.twimg.com", "pic.twitter.com",
    "imgur.com", "i.imgur.com",
    "giphy.com",
    "flickr.com",
    # Video (not readable content)
    "youtube.com", "youtu.be",
    "vimeo.com",
    "twitch.tv",
    "tiktok.com",
    # Link shorteners (we see the expanded URL, these are leftovers)
    "bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "dlvr.it",
    "lnkd.in", "rb.gy", "is.gd",
    # Shopping / app stores
    "amazon.com", "amzn.to",
    "apps.apple.com", "play.google.com",
    # File/doc hosting without content
    "drive.google.com", "docs.google.com",
    "dropbox.com",
    # Jobs / events
    "jobs.lever.co", "boards.greenhouse.io",
    "eventbrite.com", "lu.ma",
    # Misc noise
    "patreon.com", "ko-fi.com", "buymeacoffee.com",
    "open.spotify.com",
    "podcasts.apple.com",
    "discord.gg", "discord.com",
}

# File extensions that are never content
_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".mov", ".webm", ".mp3", ".wav",
    ".zip", ".tar", ".gz",
}


def _normalize_arxiv_url(arxiv_id: str) -> str:
    clean = re.sub(r"v\d+$", "", arxiv_id)
    return f"https://arxiv.org/abs/{clean}"


def _normalize_url(url: str) -> str:
    """Strip tracking params and fragments for deduplication."""
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        keep = []
        for part in parsed.query.split("&"):
            key = part.split("=")[0].lower()
            if key not in ("utm_source", "utm_medium", "utm_campaign",
                           "utm_content", "utm_term", "ref", "s", "t",
                           "source", "mc_cid", "mc_eid"):
                keep.append(part)
        if keep:
            clean += "?" + "&".join(keep)
    return clean.rstrip("/")


def _get_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().lstrip("www.")


def _should_skip(url: str) -> bool:
    """Return True if this URL is noise that should never be a reading list item."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().lstrip("www.")

    if not host:
        return True

    if host in _SKIP_DOMAINS:
        return True

    ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path.split("/")[-1] else ""
    if f".{ext}" in _SKIP_EXTENSIONS:
        return True

    # Skip bare homepages with no path (e.g. "https://openai.com")
    path = parsed.path.strip("/")
    if not path:
        return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_paper_links(urls: list[str]) -> list[PaperLink]:
    """Extract paper/technical-content links from tweet URLs.

    First tries specific academic patterns (arxiv, DOI, etc.).
    Then captures anything that looks like content (blogs, GitHub, etc.).
    """
    results: list[PaperLink] = []
    seen_ids: set[str] = set()

    for url in urls:
        link = _match_url(url)
        if link and link.paper_id not in seen_ids:
            seen_ids.add(link.paper_id)
            results.append(link)

    return results


def extract_all_links(urls: list[str]) -> list[str]:
    """Return all non-noise URLs for multi-mention detection."""
    results: list[str] = []
    for url in urls:
        if not _should_skip(url):
            results.append(_normalize_url(url))
    return results


# ---------------------------------------------------------------------------
# URL matching
# ---------------------------------------------------------------------------

def _match_url(url: str) -> PaperLink | None:
    # --- Academic papers (specific patterns, highest priority) ---

    m = _ARXIV_ID.search(url)
    if m:
        aid = m.group(1)
        return PaperLink(url=_normalize_arxiv_url(aid), source="arxiv", paper_id=aid)

    m = _ARXIV_OLD.search(url)
    if m:
        aid = m.group(1)
        return PaperLink(url=_normalize_arxiv_url(aid), source="arxiv", paper_id=aid)

    m = _DOI.search(url)
    if m:
        doi = m.group(1).rstrip(".")
        return PaperLink(url=f"https://doi.org/{doi}", source="doi", paper_id=doi)

    m = _OPENREVIEW.search(url)
    if m:
        orid = m.group(1)
        return PaperLink(url=f"https://openreview.net/forum?id={orid}", source="openreview", paper_id=orid)

    m = _SEMANTIC_SCHOLAR.search(url)
    if m:
        return PaperLink(url=url, source="semantic_scholar", paper_id=m.group(1))

    m = _ACL_ANTHOLOGY.search(url)
    if m:
        aid = m.group(1)
        return PaperLink(url=f"https://aclanthology.org/{aid}", source="acl", paper_id=aid)

    m = _NEURIPS.search(url)
    if m:
        return PaperLink(url=url, source="neurips", paper_id=f"{m.group(1)}/{m.group(2)}")

    m = _PMLR.search(url)
    if m:
        pid = m.group(1)
        return PaperLink(url=f"https://proceedings.mlr.press/{pid}.html", source="pmlr", paper_id=pid)

    m = _BIORXIV.search(url)
    if m:
        return PaperLink(url=url, source="biorxiv", paper_id=m.group(1))

    m = _NATURE.search(url)
    if m:
        return PaperLink(url=url, source="nature", paper_id=m.group(1))

    m = _SCIENCE.search(url)
    if m:
        doi = m.group(1).rstrip(".")
        return PaperLink(url=f"https://doi.org/{doi}", source="doi", paper_id=doi)

    m = _JMLR.search(url)
    if m:
        return PaperLink(url=url, source="jmlr", paper_id=m.group(1))

    m = _AAAI.search(url)
    if m:
        return PaperLink(url=url, source="aaai", paper_id=m.group(1))

    # --- Hugging Face paper pages (resolve to arxiv) ---
    host = _get_host(url)
    parsed = urlparse(url)

    if host == "huggingface.co" and "/papers/" in parsed.path:
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "papers":
            hf_id = parts[1]
            return PaperLink(url=f"https://arxiv.org/abs/{hf_id}", source="arxiv", paper_id=hf_id)

    # --- Google Scholar ---
    if host == "scholar.google.com":
        return PaperLink(url=url, source="scholar", paper_id=_normalize_url(url))

    # --- Any PDF link ---
    if parsed.path.lower().endswith(".pdf"):
        return PaperLink(url=url, source="pdf", paper_id=_normalize_url(url))

    # --- Catch-all: anything not in the skip list is content worth capturing ---
    if not _should_skip(url):
        source = "github" if host in ("github.com", "gitlab.com") else "blog"
        return PaperLink(url=url, source=source, paper_id=_normalize_url(url))

    return None
