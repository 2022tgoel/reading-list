from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Paper, Tweet
from app.paper_extractor import extract_all_links, extract_paper_links
from app.paper_metadata import resolve, resolve_url
from app.twitter import fetch_following_tweets

log = logging.getLogger(__name__)
router = APIRouter()


# ---- Pydantic schemas ----

class TweetOut(BaseModel):
    id: int
    twitter_id: str
    author_name: str
    author_handle: str
    text: str
    url: str
    tweeted_at: datetime | None

    class Config:
        from_attributes = True


class PaperOut(BaseModel):
    id: int
    title: str
    authors: str
    abstract: str
    url: str
    source: str
    year: int | None
    venue: str | None
    added_at: datetime
    is_read: bool
    is_bookmarked: bool
    tweet_count: int
    tweets: list[TweetOut]

    class Config:
        from_attributes = True


class PaperPatch(BaseModel):
    is_read: Optional[bool] = None
    is_bookmarked: Optional[bool] = None


class StatsOut(BaseModel):
    total: int
    unread: int
    bookmarked: int
    sources: dict[str, int]


class RefreshResult(BaseModel):
    tweets_fetched: int
    tweets_with_links: int
    new_papers: int
    errors: int


# ---- Endpoints ----

@router.get("/papers", response_model=list[PaperOut])
def list_papers(
    filter: str = Query("all", pattern="^(all|unread|bookmarked)$"),
    search: str = Query("", max_length=200),
    sort: str = Query("recent", pattern="^(recent|popular)$"),
    db: Session = Depends(get_db),
):
    q = db.query(Paper).options(joinedload(Paper.tweets))

    if filter == "unread":
        q = q.filter(Paper.is_read == False)
    elif filter == "bookmarked":
        q = q.filter(Paper.is_bookmarked == True)

    if search:
        like = f"%{search}%"
        q = q.filter(
            (Paper.title.ilike(like))
            | (Paper.authors.ilike(like))
            | (Paper.abstract.ilike(like))
        )

    if sort == "popular":
        q = (
            q.outerjoin(Tweet)
            .group_by(Paper.id)
            .order_by(func.count(Tweet.id).desc(), Paper.added_at.desc())
        )
    else:
        q = q.order_by(Paper.added_at.desc())

    papers = q.all()

    # Deduplicate (joinedload + outerjoin can cause dupes)
    seen = set()
    unique = []
    for p in papers:
        if p.id not in seen:
            seen.add(p.id)
            unique.append(p)

    return unique


@router.patch("/papers/{paper_id}", response_model=PaperOut)
def update_paper(
    paper_id: int,
    patch: PaperPatch,
    db: Session = Depends(get_db),
):
    paper = db.query(Paper).options(joinedload(Paper.tweets)).filter(Paper.id == paper_id).first()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    if patch.is_read is not None:
        paper.is_read = patch.is_read
    if patch.is_bookmarked is not None:
        paper.is_bookmarked = patch.is_bookmarked

    db.commit()
    db.refresh(paper)
    return paper


@router.get("/stats", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    total = db.query(Paper).count()
    unread = db.query(Paper).filter(Paper.is_read == False).count()
    bookmarked = db.query(Paper).filter(Paper.is_bookmarked == True).count()

    source_rows = (
        db.query(Paper.source, func.count(Paper.id)).group_by(Paper.source).all()
    )
    sources = {row[0]: row[1] for row in source_rows}

    return StatsOut(total=total, unread=unread, bookmarked=bookmarked, sources=sources)


@router.post("/refresh", response_model=RefreshResult)
async def refresh_papers(
    days_back: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Fetch tweets from all followed accounts and extract papers.

    Goes back `days_back` days (default 30) across every account you follow.
    """
    log.info("Starting refresh (days_back=%d)...", days_back)
    raw_tweets = fetch_following_tweets(days_back=days_back)

    new_papers = 0
    tweets_with_links = 0
    errors = 0

    # Track tweet IDs we've already inserted in this session
    inserted_tweet_ids: set[str] = set()

    # --- Pass 1: collect all URLs and track which users shared them ---
    from collections import defaultdict
    url_to_users: dict[str, set[str]] = defaultdict(set)
    url_to_tweets: dict[str, list] = defaultdict(list)

    for tweet in raw_tweets:
        all_urls = extract_all_links(tweet.urls)
        for u in all_urls:
            url_to_users[u].add(tweet.author_handle)
            url_to_tweets[u].append(tweet)

    multi_mention_urls = {u for u, users in url_to_users.items() if len(users) >= 2}
    log.info(
        "URL stats: %d unique URLs, %d shared by 2+ people.",
        len(url_to_users), len(multi_mention_urls),
    )

    # --- Pass 2: process tweets for recognized paper/technical links ---
    processed_tweet_ids: set[str] = set()

    for tweet in raw_tweets:
        links = extract_paper_links(tweet.urls)
        if not links:
            continue

        tweets_with_links += 1
        processed_tweet_ids.add(tweet.tweet_id)

        for link in links:
            new_papers, errors = await _upsert_paper_from_link(
                db, link, tweet, new_papers, errors, inserted_tweet_ids,
            )

    # --- Pass 3: capture multi-mention URLs not already handled ---
    for url in multi_mention_urls:
        existing = db.query(Paper).filter(Paper.url == url).first()
        if existing:
            for tweet in url_to_tweets[url]:
                _add_tweet_if_new(db, tweet, existing.id, inserted_tweet_ids)
            continue

        try:
            meta = await resolve_url(url, source="shared")
        except Exception:
            log.exception("Failed resolving shared URL %s", url)
            meta = None

        if not meta:
            errors += 1
            continue

        sharers = url_to_users[url]
        log.info(
            "Multi-mention link (%d people: %s): %s",
            len(sharers), ", ".join(sorted(sharers)[:5]), meta.title[:80],
        )

        paper = Paper(
            title=meta.title, authors=meta.authors, abstract=meta.abstract,
            url=url, source=meta.source, year=meta.year, venue=meta.venue,
        )
        db.add(paper)
        db.flush()

        for tweet in url_to_tweets[url]:
            _add_tweet_if_new(db, tweet, paper.id, inserted_tweet_ids)
        new_papers += 1

    db.commit()
    log.info(
        "Refresh done: %d tweets, %d with paper links, %d new papers, %d errors.",
        len(raw_tweets), tweets_with_links, new_papers, errors,
    )
    return RefreshResult(
        tweets_fetched=len(raw_tweets),
        tweets_with_links=tweets_with_links,
        new_papers=new_papers,
        errors=errors,
    )


def _add_tweet_if_new(db, tweet, paper_id, inserted_tweet_ids):
    """Add a Tweet row if we haven't already inserted it in this session or DB."""
    if tweet.tweet_id in inserted_tweet_ids:
        return
    existing = db.query(Tweet).filter(Tweet.twitter_id == tweet.tweet_id).first()
    if existing:
        inserted_tweet_ids.add(tweet.tweet_id)
        return
    db.add(Tweet(
        twitter_id=tweet.tweet_id,
        author_name=tweet.author_name,
        author_handle=tweet.author_handle,
        text=tweet.text,
        url=f"https://twitter.com/{tweet.author_handle}/status/{tweet.tweet_id}",
        tweeted_at=tweet.tweeted_at,
        paper_id=paper_id,
    ))
    inserted_tweet_ids.add(tweet.tweet_id)


async def _upsert_paper_from_link(db, link, tweet, new_papers, errors, inserted_tweet_ids):
    """Insert or update a paper from a recognized PaperLink."""
    existing = db.query(Paper).filter(Paper.url == link.url).first()

    if existing:
        _add_tweet_if_new(db, tweet, existing.id, inserted_tweet_ids)
        return new_papers, errors

    try:
        meta = await resolve(link)
    except Exception:
        log.exception("Failed resolving %s", link.url)
        meta = None

    if not meta:
        log.warning("Could not resolve metadata for %s", link.url)
        return new_papers, errors + 1

    paper = Paper(
        title=meta.title, authors=meta.authors, abstract=meta.abstract,
        url=meta.url, source=meta.source, year=meta.year, venue=meta.venue,
    )
    db.add(paper)
    db.flush()

    _add_tweet_if_new(db, tweet, paper.id, inserted_tweet_ids)
    new_papers += 1
    log.info("New paper: %s", meta.title[:80])
    return new_papers, errors
