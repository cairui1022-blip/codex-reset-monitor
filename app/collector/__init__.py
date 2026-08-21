"""
采集层 - 通过 Twitter API v2 拉取 Tibo (@thsottiaux) 的推特时间线
依赖：tweepy >= 4.0（仅使用 Bearer Token，免费版 Basic 即可）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RawTweet:
    tweet_id: str
    author: str
    text: str
    url: str
    created_at: datetime


def _normalize_tweet_url(tweet_id: str) -> str:
    return f"https://x.com/{settings.tibo_username}/status/{tweet_id}"


def _get_user_id() -> Optional[str]:
    """
    通过 Bearer Token 查询 @thsottiaux 的 user_id。
    优先用 settings.tibo_user_id（硬编码），避免多一次 API 请求。
    """
    if settings.tibo_user_id:
        return settings.tibo_user_id
    try:
        import tweepy
        client = tweepy.Client(bearer_token=settings.twitter_bearer_token, wait_on_rate_limit=False)
        resp = client.get_user(username=settings.tibo_username)
        if resp.data:
            uid = str(resp.data.id)
            logger.info("Resolved @%s -> user_id %s", settings.tibo_username, uid)
            return uid
    except Exception as e:
        logger.error("Failed to get user_id for @%s: %s", settings.tibo_username, e)
    return None


def fetch_recent_tweets(max_results: int = 10) -> list[RawTweet]:
    """
    通过 Twitter API v2 拉取最新推文。
    需要环境变量 TWITTER_BEARER_TOKEN。
    """
    if not settings.twitter_bearer_token:
        logger.error("TWITTER_BEARER_TOKEN not set, cannot fetch tweets.")
        return []

    try:
        import tweepy
    except ImportError:
        logger.error("tweepy not installed. Run: pip install tweepy")
        return []

    user_id = _get_user_id()
    if not user_id:
        logger.error("Cannot resolve user_id, aborting fetch.")
        return []

    try:
        client = tweepy.Client(
            bearer_token=settings.twitter_bearer_token,
            wait_on_rate_limit=False,
        )
        # exclude_replies=True, exclude_retweets=True 避免噪音
        resp = client.get_users_tweets(
            id=user_id,
            max_results=max(5, min(max_results, 100)),
            tweet_fields=["created_at", "text", "id"],
            exclude=["replies", "retweets"],
        )
    except Exception as e:
        logger.error("Twitter API error: %s", e)
        return []

    if not resp.data:
        logger.warning("Twitter API returned no tweets for user_id %s", user_id)
        return []

    tweets: list[RawTweet] = []
    for t in resp.data:
        created = t.created_at
        if created is None:
            created = datetime.utcnow()
        elif created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)

        tweets.append(RawTweet(
            tweet_id=str(t.id),
            author=settings.tibo_username,
            text=t.text,
            url=_normalize_tweet_url(str(t.id)),
            created_at=created,
        ))

    logger.info("Fetched %d tweets via Twitter API v2", len(tweets))
    return sorted(tweets, key=lambda x: x.created_at, reverse=True)[:max_results]
