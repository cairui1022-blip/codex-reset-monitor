"""
采集层 - 通过 Playwright 无头浏览器抓取 x.com/@thsottiaux 时间线
策略：拦截浏览器发出的 GraphQL XHR 请求，提取 JSON 推文数据。

重要：Playwright sync_api 不能跨线程/greenlet 使用（FastAPI 在线程池中调用）。
解决方案：在独立子进程（ProcessPoolExecutor）中运行 Playwright，
子进程有自己干净的 main 线程，不存在 greenlet 冲突。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

# 全局进程池（单进程，避免资源争用）
_EXECUTOR = None
_EXECUTOR_LOCK = __import__("threading").Lock()

# x.com GraphQL 端点关键字（用于拦截响应）
_GRAPHQL_TIMELINE_KEYWORDS = [
    "UserTweets",
    "UserMedia",
    "TweetDetail",
]

# 目标用户名
_USERNAME = settings.tibo_username

# 请求超时（ms / s）
_NAV_TIMEOUT = 45_000
_WAIT_TIMEOUT = 25


@dataclass
class RawTweet:
    tweet_id: str
    author: str
    text: str
    url: str
    created_at: datetime


# ─────────────────────────────────────────────
# 子进程内执行的函数（必须是模块级 top-level）
# ─────────────────────────────────────────────

def _playwright_worker(username: str, max_results: int) -> list[dict]:
    """
    在子进程中运行 Playwright。返回可序列化的 dict 列表。
    这个函数必须是模块级别（不能是 lambda / 嵌套函数），
    因为 ProcessPoolExecutor 需要 pickle 它。
    """
    import os as _os
    import json as _json
    import threading
    from playwright.sync_api import sync_playwright

    # 显式设置 Playwright browser 路径，确保子进程能找到 Chromium
    # Render 部署时 browser 安装在 /opt/render/project/src/.playwright-browsers
    _playwright_path = "/opt/render/project/src/.playwright-browsers"
    _os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _playwright_path)
    # 也覆盖写入，防止被默认值覆盖
    if not _os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        _os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _playwright_path

    collected: list[dict] = []
    hit_event = threading.Event()

    _graphql_kws = ["UserTweets", "UserMedia", "TweetDetail"]

    def _extract(data: dict) -> list[dict]:
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
                        from datetime import timezone as tz
                        dt = parsedate_to_datetime(created_at_str)
                        if dt.tzinfo is not None:
                            dt = dt.astimezone(tz.utc).replace(tzinfo=None)
                        ts = dt.isoformat()
                    except Exception:
                        from datetime import datetime as _dt
                        ts = _dt.utcnow().isoformat()
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

    def _on_response(response):
        try:
            url = response.url
            if not any(kw in url for kw in _graphql_kws):
                return
            if response.status != 200:
                return
            body = response.text()
            data = _json.loads(body)
            tweets = _extract(data)
            if tweets:
                collected.extend(tweets)
                hit_event.set()
        except Exception:
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--single-process",
                "--disable-extensions",
            ],
        )
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
        page = context.new_page()
        page.on("response", _on_response)

        try:
            page.goto(
                f"https://x.com/{username}",
                timeout=45_000,
                wait_until="domcontentloaded",
            )
            # 等待 GraphQL 命中（最多 25 秒）
            hit_event.wait(timeout=25)
            if not collected:
                # 尝试滚动触发加载
                page.evaluate("window.scrollBy(0, 600)")
                hit_event.wait(timeout=8)
        except Exception:
            pass
        finally:
            try:
                page.close()
                context.close()
                browser.close()
            except Exception:
                pass

    # 去重 + 排序
    seen: set[str] = set()
    unique: list[dict] = []
    for t in collected:
        if t["tweet_id"] not in seen:
            seen.add(t["tweet_id"])
            unique.append(t)

    unique.sort(key=lambda x: x["created_at"], reverse=True)
    return unique[:max_results]


# ─────────────────────────────────────────────
# 进程池管理
# ─────────────────────────────────────────────

def _get_executor():
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            from concurrent.futures import ProcessPoolExecutor
            _EXECUTOR = ProcessPoolExecutor(max_workers=1)
    return _EXECUTOR


# ─────────────────────────────────────────────
# 公共接口
# ─────────────────────────────────────────────

def _fetch_via_playwright(max_results: int = 20) -> list[RawTweet]:
    """
    在隔离子进程中运行 Playwright，避免 greenlet/thread 冲突。
    """
    executor = _get_executor()
    future = executor.submit(_playwright_worker, _USERNAME, max_results)
    # 阻塞等待（最多 60 秒）
    raw_list: list[dict] = future.result(timeout=60)

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
    优先使用 Playwright 无头浏览器拦截 GraphQL 响应。
    失败时返回空列表。
    """
    try:
        return _fetch_via_playwright(max_results=max_results)
    except Exception as e:
        logger.error("Playwright collector failed: %s", e, exc_info=True)
        return []


# 保留常量供兼容（向下兼容旧代码）
NITTER_INSTANCES: list[str] = []
_HEADERS: dict = {}
_TIMEOUT: int = 30
