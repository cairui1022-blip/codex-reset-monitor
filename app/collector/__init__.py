"""
采集层 - 通过 Playwright 无头浏览器抓取 x.com/@thsottiaux 时间线
策略：拦截浏览器发出的 GraphQL XHR 请求，提取 JSON 推文数据。
无需 API Key，无需登录。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# 全局 Playwright 实例（懒加载，线程安全通过锁控制）
_pw_lock = threading.Lock()
_browser = None  # playwright Browser 对象

# x.com GraphQL 端点关键字（用于拦截响应）
_GRAPHQL_TIMELINE_KEYWORDS = [
    "UserTweets",
    "UserMedia",
    "TweetDetail",
]

# 目标用户名
_USERNAME = settings.tibo_username

# 请求超时（ms）
_NAV_TIMEOUT = 45_000
_WAIT_TIMEOUT = 20_000


@dataclass
class RawTweet:
    tweet_id: str
    author: str
    text: str
    url: str
    created_at: datetime


# ─────────────────────────────────────────────
# Playwright 浏览器管理
# ─────────────────────────────────────────────

def _get_browser():
    """懒加载 Playwright Chromium（进程级单例）"""
    global _browser
    with _pw_lock:
        if _browser is None or not _browser.is_connected():
            logger.info("Launching Playwright Chromium...")
            from playwright.sync_api import sync_playwright
            # 注意：sync_playwright 不能跨线程共享 context，但 browser 可以
            # 这里保存 playwright 对象避免被 GC
            import builtins
            if not hasattr(builtins, "_pw_instance"):
                builtins._pw_instance = sync_playwright().start()
            pw = builtins._pw_instance
            _browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process",  # Render 容器内存受限
                    "--disable-extensions",
                ],
            )
            logger.info("Playwright Chromium launched.")
    return _browser


def _parse_tweet_from_result(result: dict) -> Optional[RawTweet]:
    """从 GraphQL result 节点里提取推文字段"""
    try:
        legacy = result.get("legacy", {})
        tweet_id = legacy.get("id_str") or result.get("rest_id", "")
        if not tweet_id:
            return None

        # 跳过转发（full_text 以 "RT @" 开头）
        full_text: str = legacy.get("full_text", "") or legacy.get("text", "")
        if full_text.startswith("RT @"):
            return None

        # 解析时间
        created_at_str: str = legacy.get("created_at", "")
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(created_at_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            dt = datetime.utcnow()

        author = _USERNAME
        url = f"https://x.com/{author}/status/{tweet_id}"
        return RawTweet(
            tweet_id=tweet_id,
            author=author,
            text=full_text,
            url=url,
            created_at=dt,
        )
    except Exception as e:
        logger.debug("Failed to parse tweet result: %s", e)
        return None


def _extract_tweets_from_graphql(data: dict) -> list[RawTweet]:
    """递归遍历 GraphQL JSON，找出所有 tweet_results 节点"""
    tweets: list[RawTweet] = []

    def _walk(obj):
        if isinstance(obj, dict):
            # tweet_results 节点
            if "tweet_results" in obj:
                result = obj["tweet_results"].get("result", {})
                # 可能有 __typename == "Tweet" 或 "TweetWithVisibilityResults"
                if result.get("__typename") == "TweetWithVisibilityResults":
                    result = result.get("tweet", result)
                t = _parse_tweet_from_result(result)
                if t:
                    tweets.append(t)
            else:
                for v in obj.values():
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return tweets


def _fetch_via_playwright(max_results: int = 20) -> list[RawTweet]:
    """
    使用 Playwright 打开 x.com 用户页面，
    拦截 GraphQL UserTweets 响应，提取推文 JSON。
    """
    browser = _get_browser()
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        java_script_enabled=True,
    )

    collected_tweets: list[RawTweet] = []
    graphql_hit = threading.Event()

    def _on_response(response):
        """在后台线程里同步处理 network response"""
        try:
            url = response.url
            if not any(kw in url for kw in _GRAPHQL_TIMELINE_KEYWORDS):
                return
            if response.status != 200:
                return
            body_text = response.text()
            data = json.loads(body_text)
            tweets = _extract_tweets_from_graphql(data)
            if tweets:
                logger.info("Intercepted GraphQL response: %d tweets from %s", len(tweets), url[:80])
                collected_tweets.extend(tweets)
                graphql_hit.set()
        except Exception as e:
            logger.debug("Response handler error: %s", e)

    page = context.new_page()
    page.on("response", _on_response)

    target_url = f"https://x.com/{_USERNAME}"
    logger.info("Navigating to %s", target_url)

    try:
        page.goto(target_url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
        # 等待 GraphQL 响应（最多 20 秒）
        graphql_hit.wait(timeout=_WAIT_TIMEOUT / 1000)

        if not collected_tweets:
            # 尝试滚动触发加载
            page.evaluate("window.scrollBy(0, 500)")
            graphql_hit.wait(timeout=5)

    except Exception as e:
        logger.warning("Playwright navigation/wait error: %s", e)
    finally:
        try:
            page.close()
            context.close()
        except Exception:
            pass

    # 去重 + 排序
    seen: set[str] = set()
    unique: list[RawTweet] = []
    for t in collected_tweets:
        if t.tweet_id not in seen:
            seen.add(t.tweet_id)
            unique.append(t)

    unique.sort(key=lambda x: x.created_at, reverse=True)
    logger.info("Playwright collector done: %d unique tweets", len(unique))
    return unique[:max_results]


# ─────────────────────────────────────────────
# 公共接口
# ─────────────────────────────────────────────

def fetch_recent_tweets(max_results: int = 20) -> list[RawTweet]:
    """
    采集 @thsottiaux 最新推文。
    优先使用 Playwright 无头浏览器拦截 GraphQL 响应。
    失败时返回空列表。
    """
    try:
        return _fetch_via_playwright(max_results=max_results)
    except Exception as e:
        logger.error("Playwright collector failed: %s", e, exc_info=True)
        return []


# 保留常量供 debug 接口使用（向下兼容）
NITTER_INSTANCES: list[str] = []
_HEADERS: dict = {}
_TIMEOUT: int = 30
