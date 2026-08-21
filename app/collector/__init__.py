"""
采集层 - 直接调用 X.com GraphQL API 获取 @thsottiaux 时间线推文。

策略：使用 curl_cffi 库（TLS 指纹伪装），直接发送与浏览器一样的 HTTPS 请求，
无需 Playwright / 无头浏览器。

X.com 的 UserTweets GraphQL 端点对未登录请求返回公开推文，
需要：
  - gt (guest token): 通过 POST /1.1/guest/activate.json 获取
  - bearer token: 固定公开值（浏览器 JS 中硬编码）
  - 正确的 TLS 指纹：curl_cffi 使用 impersonate="chrome124" 实现
"""
from __future__ import annotations

import json
import logging
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# X.com 公开 Bearer Token（浏览器 JS 中硬编码，无需登录）
_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

# GraphQL endpoint
_GRAPHQL_URL = "https://api.twitter.com/graphql/V7H0Ap3_Hh2FyS75OCDO3Q/UserTweets"

# 目标用户
_USERNAME = settings.tibo_username
_USER_ID = getattr(settings, "tibo_user_id", "1267893715938840576")

# Guest token 缓存（有效期约 3 小时）
_guest_token: Optional[str] = None
_guest_token_ts: float = 0.0
_GUEST_TOKEN_TTL = 10800  # 3 hours


@dataclass
class RawTweet:
    tweet_id: str
    author: str
    text: str
    url: str
    created_at: datetime


def _get_cffi_session():
    """Create a curl_cffi session with Chrome TLS fingerprint."""
    try:
        from curl_cffi.requests import Session
        session = Session(impersonate="chrome124")
        return session
    except ImportError:
        logger.error("curl_cffi not installed, falling back to requests")
        import requests
        return requests.Session()


def _get_guest_token(session) -> str:
    """Get or refresh the guest token from X.com."""
    global _guest_token, _guest_token_ts

    now = time.time()
    if _guest_token and (now - _guest_token_ts) < _GUEST_TOKEN_TTL:
        return _guest_token

    headers = {
        "Authorization": f"Bearer {_BEARER_TOKEN}",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://x.com",
        "Referer": "https://x.com/",
    }
    resp = session.post(
        "https://api.twitter.com/1.1/guest/activate.json",
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("guest_token") or data.get("guest_Token")
    if not token:
        raise ValueError(f"No guest_token in response: {data}")

    _guest_token = token
    _guest_token_ts = now
    logger.info("Refreshed guest token: %s…", token[:8])
    return token


def _extract_tweets_from_response(data: dict, username: str) -> list[dict]:
    """Recursively walk the GraphQL response and extract tweet dicts."""
    results = []

    def _walk(obj):
        if isinstance(obj, dict):
            if "tweet_results" in obj:
                result = obj["tweet_results"].get("result", {})
                if result.get("__typename") == "TweetWithVisibilityResults":
                    result = result.get("tweet", result)
                legacy = result.get("legacy", {})
                tweet_id = legacy.get("id_str") or result.get("rest_id", "")
                if not tweet_id:
                    return
                full_text = legacy.get("full_text", "") or legacy.get("text", "")
                if full_text.startswith("RT @"):
                    return
                created_at_str = legacy.get("created_at", "")
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(created_at_str)
                    if dt.tzinfo is not None:
                        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    ts = dt.isoformat()
                except Exception:
                    ts = datetime.utcnow().isoformat()
                results.append({
                    "tweet_id": tweet_id,
                    "author": username,
                    "text": full_text,
                    "url": f"https://x.com/{username}/status/{tweet_id}",
                    "created_at": ts,
                })
            else:
                for v in obj.values():
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return results


def _fetch_via_graphql(max_results: int = 20) -> list[RawTweet]:
    """
    Fetch tweets via X.com GraphQL API using curl_cffi (TLS fingerprint spoofing).
    No browser needed.
    """
    session = _get_cffi_session()

    guest_token = _get_guest_token(session)

    variables = {
        "userId": _USER_ID,
        "count": max(max_results, 20),
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    features = {
        "rweb_lists_timeline_redesign_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": False,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": True,
        "responsive_web_media_download_video_enabled": False,
        "responsive_web_enhance_cards_enabled": False,
    }

    headers = {
        "Authorization": f"Bearer {_BEARER_TOKEN}",
        "X-Guest-Token": guest_token,
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://x.com",
        "Referer": f"https://x.com/{_USERNAME}",
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Client-Language": "en",
    }

    params = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": json.dumps(features, separators=(",", ":")),
    }

    resp = session.get(
        _GRAPHQL_URL,
        headers=headers,
        params=params,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    raw_list = _extract_tweets_from_response(data, _USERNAME)

    # Deduplicate + sort
    seen: set[str] = set()
    unique: list[dict] = []
    for t in raw_list:
        if t["tweet_id"] not in seen:
            seen.add(t["tweet_id"])
            unique.append(t)

    unique.sort(key=lambda x: x["created_at"], reverse=True)
    raw_list = unique[:max_results]

    tweets: list[RawTweet] = []
    for r in raw_list:
        try:
            dt = datetime.fromisoformat(r["created_at"])
        except Exception:
            dt = datetime.utcnow()
        tweets.append(RawTweet(
            tweet_id=r["tweet_id"],
            author=r["author"],
            text=r["text"],
            url=r["url"],
            created_at=dt,
        ))
    return tweets


def fetch_recent_tweets(max_results: int = 20) -> list[RawTweet]:
    """
    采集 @thsottiaux 最新推文。
    使用 curl_cffi 直接调用 X.com GraphQL API（TLS 指纹伪装）。
    失败时返回空列表。
    """
    try:
        return _fetch_via_graphql(max_results=max_results)
    except Exception as e:
        logger.error("curl_cffi GraphQL collector failed: %s", e, exc_info=True)
        return []


# ─────────────────────────────────────────────
# 公共调试接口（供 debug API 端点使用）
# ─────────────────────────────────────────────

def debug_fetch(max_results: int = 5) -> dict:
    """Return a debug dict with method, tweets, and error info."""
    import traceback
    start = time.time()
    error = None
    tweets = []
    try:
        raw = _fetch_via_graphql(max_results=max_results)
        tweets = [
            {"tweet_id": t.tweet_id, "text": t.text[:120], "created_at": t.created_at.isoformat()}
            for t in raw
        ]
    except Exception as e:
        error = traceback.format_exc()
    elapsed = round(time.time() - start, 2)
    return {
        "method": "curl_cffi_graphql",
        "tibo_username": _USERNAME,
        "tibo_user_id": _USER_ID,
        "elapsed_sec": elapsed,
        "tweets_fetched": len(tweets),
        "samples": tweets,
        "error": error,
    }


# 向下兼容旧代码保留的常量
NITTER_INSTANCES: list[str] = []
_HEADERS: dict = {}
_TIMEOUT: int = 30
