"""
采集层 - 通过 RSSHub 拉取 Tibo (@thsottiaux) 的推特时间线
兜底：直接抓 nitter 公共实例 RSS
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
import feedparser
import requests

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class RawTweet:
    tweet_id: str
    author: str
    text: str
    url: str
    created_at: datetime


# ─────────────────────────────────────────────
# RSSHub 实例列表（按顺序尝试，fallback 到 nitter）
# ─────────────────────────────────────────────

RSSHUB_INSTANCES = [
    settings.rsshub_base_url,
    "https://rsshub.rssforever.com",
    "https://hub.slarker.me",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.it",
    "https://nitter.privacydev.net",
]


def _make_proxies() -> Optional[dict]:
    if settings.http_proxy or settings.https_proxy:
        return {
            "http": settings.http_proxy or settings.https_proxy,
            "https": settings.https_proxy or settings.http_proxy,
        }
    return None


def _parse_tweet_id_from_url(url: str) -> Optional[str]:
    """从推文 URL 提取 tweet_id，兼容 x.com / twitter.com / nitter"""
    m = re.search(r"/status(?:es)?/(\d+)", url)
    return m.group(1) if m else None


def _normalize_tweet_url(tweet_id: str) -> str:
    return f"https://x.com/{settings.tibo_username}/status/{tweet_id}"


def _fetch_feed(url: str, timeout: int = 15) -> Optional[feedparser.FeedParserDict]:
    proxies = _make_proxies()
    try:
        headers = {"User-Agent": "codex-reset-monitor/1.0"}
        if proxies:
            resp = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
        else:
            resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            logger.warning("Feed parse warning for %s: %s", url, feed.bozo_exception)
            return None
        return feed
    except Exception as e:
        logger.warning("Failed to fetch feed %s: %s", url, e)
        return None


def _parse_entries(feed: feedparser.FeedParserDict) -> list[RawTweet]:
    tweets: list[RawTweet] = []
    for entry in feed.entries:
        link = entry.get("link", "")
        tweet_id = _parse_tweet_id_from_url(link)
        if not tweet_id:
            continue

        # 解析时间
        published = entry.get("published") or entry.get("updated")
        try:
            if published:
                dt = parsedate_to_datetime(published)
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                dt = datetime.utcnow()
        except Exception:
            dt = datetime.utcnow()

        # 清理 HTML 标签
        raw_text = entry.get("summary", entry.get("title", ""))
        clean_text = re.sub(r"<[^>]+>", " ", raw_text).strip()
        clean_text = re.sub(r"\s+", " ", clean_text)

        tweets.append(RawTweet(
            tweet_id=tweet_id,
            author=settings.tibo_username,
            text=clean_text,
            url=_normalize_tweet_url(tweet_id),
            created_at=dt,
        ))
    return tweets


def fetch_recent_tweets(max_results: int = 10) -> list[RawTweet]:
    """
    按优先级尝试多个 RSS 数据源：
      1. RSSHub 实例（/twitter/user/:username）
      2. Nitter 实例（/:username/rss）
    返回最新 max_results 条，按时间降序。
    """
    username = settings.tibo_username

    # --- 尝试 RSSHub ---
    for base in RSSHUB_INSTANCES:
        url = f"{base.rstrip('/')}/twitter/user/{username}"
        logger.debug("Trying RSSHub: %s", url)
        feed = _fetch_feed(url)
        if feed and feed.entries:
            tweets = _parse_entries(feed)
            if tweets:
                logger.info("Fetched %d tweets via RSSHub (%s)", len(tweets), base)
                return sorted(tweets, key=lambda t: t.created_at, reverse=True)[:max_results]

    # --- fallback: Nitter ---
    for base in NITTER_INSTANCES:
        url = f"{base.rstrip('/')}/{username}/rss"
        logger.debug("Trying Nitter: %s", url)
        feed = _fetch_feed(url)
        if feed and feed.entries:
            tweets = _parse_entries(feed)
            if tweets:
                logger.info("Fetched %d tweets via Nitter (%s)", len(tweets), base)
                return sorted(tweets, key=lambda t: t.created_at, reverse=True)[:max_results]

    logger.error("All RSS sources failed for @%s", username)
    return []
