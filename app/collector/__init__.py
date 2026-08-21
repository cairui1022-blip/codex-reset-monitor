"""
采集层 - 通过 Nitter RSS 拉取 Tibo (@thsottiaux) 的推特时间线
多实例轮询，任意一个成功即返回。无需 API Key。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)

# Nitter 实例列表（按优先级排序，Render 服务器在美国，优先选美国/欧洲实例）
NITTER_INSTANCES = [
    "nitter.net",
    "nitter.poast.org",
    "nitter.cz",
    "nitter.privacydev.net",
    "nitter.fdn.fr",
    "nitter.1d4.us",
    "nitter.nixnet.services",
    "nitter.unixfox.eu",
]

# 请求超时（秒）
_TIMEOUT = 12

# User-Agent 模拟普通浏览器，避免被 Cloudflare 屏蔽
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


@dataclass
class RawTweet:
    tweet_id: str
    author: str
    text: str
    url: str
    created_at: datetime


def _parse_tweet_id_from_url(url: str) -> Optional[str]:
    """从推文 URL 中提取 tweet_id"""
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else None


def _parse_pub_date(date_str: str) -> datetime:
    """将 RSS pubDate 字符串解析为 naive UTC datetime"""
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return datetime.utcnow()


def _fetch_from_instance(host: str, username: str) -> list[RawTweet]:
    """从单个 Nitter 实例拉取 RSS，返回 RawTweet 列表；失败抛异常"""
    url = f"https://{host}/{username}/rss"
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
    resp.raise_for_status()

    # feedparser 解析
    import feedparser
    feed = feedparser.parse(resp.text)
    if not feed.entries:
        # 区分"真的无推文"和"被 Cloudflare 拦截返回 HTML"
        if "<html" in resp.text.lower():
            raise ValueError(f"{host} returned HTML (likely blocked by Cloudflare)")
        logger.warning("%s: RSS parsed OK but 0 entries", host)
        return []

    tweets: list[RawTweet] = []
    for entry in feed.entries:
        link = getattr(entry, "link", "") or ""
        tweet_id = _parse_tweet_id_from_url(link)
        if not tweet_id:
            continue
        # 清理 Nitter 在摘要里加的 "R to @x:" / "RT by @x:" 等前缀
        summary = getattr(entry, "summary", "") or getattr(entry, "title", "") or ""
        # strip HTML tags
        text = re.sub(r"<[^>]+>", " ", summary).strip()
        text = re.sub(r"\s+", " ", text)

        pub_date = getattr(entry, "published", None)
        created_at = _parse_pub_date(pub_date) if pub_date else datetime.utcnow()

        tweets.append(RawTweet(
            tweet_id=tweet_id,
            author=username,
            text=text,
            url=link,
            created_at=created_at,
        ))

    logger.info("%s: fetched %d tweets via Nitter RSS", host, len(tweets))
    return tweets


def fetch_recent_tweets(max_results: int = 20) -> list[RawTweet]:
    """
    依次尝试 NITTER_INSTANCES，返回第一个成功实例的推文列表。
    全部失败时返回空列表并记录日志。
    """
    username = settings.tibo_username
    last_error: str = ""

    for host in NITTER_INSTANCES:
        try:
            tweets = _fetch_from_instance(host, username)
            # 即使 0 条也算成功（用户可能暂时没推文）
            return sorted(tweets, key=lambda x: x.created_at, reverse=True)[:max_results]
        except Exception as e:
            last_error = f"{host}: {e}"
            logger.warning("Nitter instance failed: %s", last_error)
            continue

    logger.error("All Nitter instances failed. Last error: %s", last_error)
    return []
