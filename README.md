# Paper Reading List

A web app that monitors your Twitter/X timeline and builds a personalized
academic paper reading list. It detects paper links (arxiv, DOI, OpenReview,
Semantic Scholar, ACL Anthology, and more), fetches metadata automatically, and
presents everything in a clean, filterable interface.

## How it works

1. **Fetch** -- pulls your home timeline via the Twitter/X API v2
2. **Extract** -- finds paper URLs in tweet text and expanded links
3. **Resolve** -- fetches title, authors, abstract, and venue from the arxiv API
   and Semantic Scholar API (both free)
4. **Present** -- serves a single-page web UI where you can search, filter,
   bookmark, and mark papers as read

Papers mentioned by multiple people float to the top when sorted by popularity.

## Prerequisites

- Python 3.11+
- **Twitter/X OAuth 1.0a credentials** (API Key, API Secret, Access Token, and
  Access Token Secret -- get them at <https://developer.x.com/en/portal/dashboard>)

## Quick start

```bash
# Clone and enter the project
cd reading-list

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure your Twitter credentials
cp .env.example .env
# Edit .env and fill in all four TWITTER_* values

# Run the app
python -m app.main
```

Then open <http://localhost:8000> and click **Refresh** to pull papers from your
timeline.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/papers` | List papers (query params: `filter`, `search`, `sort`) |
| `PATCH` | `/api/papers/{id}` | Update a paper (`is_read`, `is_bookmarked`) |
| `GET` | `/api/stats` | Summary statistics |
| `POST` | `/api/refresh` | Fetch new tweets and extract papers |

## Supported paper sources

- arxiv.org (abs, pdf, html links)
- doi.org
- OpenReview
- Semantic Scholar
- ACL Anthology
- NeurIPS proceedings
- PMLR (ICML, AISTATS, etc.)
- Hugging Face paper pages (resolved to arxiv)
