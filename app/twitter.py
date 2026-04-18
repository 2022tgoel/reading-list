from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

import tweepy

from app.config import (
    TWITTER_BEARER_TOKEN,
    TWITTER_API_KEY,
    TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN,
    TWITTER_ACCESS_TOKEN_SECRET,
)

log = logging.getLogger(__name__)


@dataclass
class RawTweet:
    tweet_id: str
    author_name: str
    author_handle: str
    text: str
    urls: list[str] = field(default_factory=list)
    tweeted_at: datetime.datetime | None = None


@dataclass
class FollowedUser:
    user_id: str
    name: str
    username: str


def _build_client() -> tweepy.Client:
    """Build a tweepy Client using whichever credentials are available.

    Prefers Bearer Token (works for all read-only endpoints).
    Falls back to OAuth 1.0a if Bearer Token is not set.
    """
    has_bearer = bool(TWITTER_BEARER_TOKEN)
    has_oauth = all([TWITTER_API_KEY, TWITTER_API_SECRET,
                     TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET])

    if not has_bearer and not has_oauth:
        raise RuntimeError(
            "No Twitter credentials found. Set TWITTER_BEARER_TOKEN "
            "or all four OAuth 1.0a keys in your .env file."
        )

    return tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN if has_bearer else None,
        consumer_key=TWITTER_API_KEY if has_oauth else None,
        consumer_secret=TWITTER_API_SECRET if has_oauth else None,
        access_token=TWITTER_ACCESS_TOKEN if has_oauth else None,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET if has_oauth else None,
        wait_on_rate_limit=True,
    )


def _get_me(client: tweepy.Client) -> str:
    """Return the authenticated user's ID."""
    resp = client.get_me()
    if not resp.data:
        raise RuntimeError("Could not retrieve authenticated user.")
    log.info("Authenticated as @%s (id=%s)", resp.data.username, resp.data.id)
    return str(resp.data.id)


def get_following(client: tweepy.Client, user_id: str) -> list[FollowedUser]:
    """Fetch all accounts the authenticated user follows (paginated)."""
    following: list[FollowedUser] = []
    pagination_token = None

    while True:
        resp = client.get_users_following(
            id=user_id,
            max_results=1000,
            pagination_token=pagination_token,
            user_fields=["name", "username"],
        )
        if resp.data:
            for u in resp.data:
                following.append(
                    FollowedUser(
                        user_id=str(u.id), name=u.name, username=u.username
                    )
                )

        meta = resp.meta or {}
        pagination_token = meta.get("next_token")
        if not pagination_token:
            break

    log.info("You follow %d accounts.", len(following))
    return following


def get_user_tweets(
    client: tweepy.Client,
    user: FollowedUser,
    since: datetime.datetime,
    max_pages: int = 5,
) -> list[RawTweet]:
    """Fetch tweets from a single user going back to `since`.

    Paginates up to `max_pages` pages of 100 tweets each (up to 500 tweets
    per user), stopping early once we pass the `since` cutoff.
    """
    results: list[RawTweet] = []
    pagination_token = None

    for _ in range(max_pages):
        try:
            resp = client.get_users_tweets(
                id=user.user_id,
                max_results=100,
                start_time=since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                pagination_token=pagination_token,
                tweet_fields=["created_at", "author_id", "entities", "referenced_tweets"],
                expansions=["author_id", "referenced_tweets.id", "referenced_tweets.id.author_id"],
                user_fields=["name", "username"],
            )
        except tweepy.TooManyRequests:
            log.warning("Rate limited fetching @%s, moving on.", user.username)
            break
        except tweepy.TwitterServerError:
            log.warning("Twitter server error for @%s, skipping.", user.username)
            break

        if not resp.data:
            break

        # Build lookup for referenced (retweeted/quoted) tweets
        ref_tweets_by_id: dict[str, tweepy.Tweet] = {}
        if resp.includes and "tweets" in resp.includes:
            for rt in resp.includes["tweets"]:
                ref_tweets_by_id[str(rt.id)] = rt

        for tweet in resp.data:
            # Collect URLs from the tweet itself
            expanded_urls: list[str] = []
            entities = tweet.data.get("entities", {})
            for url_entity in entities.get("urls", []):
                expanded = url_entity.get("expanded_url") or url_entity.get("url", "")
                if expanded:
                    expanded_urls.append(expanded)

            # For retweets/quotes, also collect URLs from the original tweet
            for ref in (tweet.data.get("referenced_tweets") or []):
                ref_tweet = ref_tweets_by_id.get(str(ref["id"]))
                if ref_tweet:
                    ref_entities = ref_tweet.data.get("entities", {})
                    for url_entity in ref_entities.get("urls", []):
                        expanded = url_entity.get("expanded_url") or url_entity.get("url", "")
                        if expanded:
                            expanded_urls.append(expanded)

            results.append(
                RawTweet(
                    tweet_id=str(tweet.id),
                    author_name=user.name,
                    author_handle=user.username,
                    text=tweet.text,
                    urls=expanded_urls,
                    tweeted_at=tweet.created_at,
                )
            )

        meta = resp.meta or {}
        pagination_token = meta.get("next_token")
        if not pagination_token:
            break

    return results


def fetch_following_tweets(days_back: int = 30) -> list[RawTweet]:
    """Fetch tweets from all accounts you follow, going back `days_back` days."""
    client = _build_client()
    my_id = _get_me(client)
    following = get_following(client, my_id)

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)

    all_tweets: list[RawTweet] = []
    for i, user in enumerate(following, 1):
        log.info(
            "[%d/%d] Fetching tweets from @%s ...", i, len(following), user.username
        )
        tweets = get_user_tweets(client, user, since=since)
        url_tweets = [t for t in tweets if t.urls]
        if url_tweets:
            log.info(
                "  -> %d tweets, %d with URLs", len(tweets), len(url_tweets)
            )
        all_tweets.extend(tweets)

    log.info(
        "Done: %d total tweets from %d accounts.",
        len(all_tweets),
        len(following),
    )
    return all_tweets
