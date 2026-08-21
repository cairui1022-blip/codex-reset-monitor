"""
调度器 - 定时轮询主循环
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import store
from app.collector import fetch_recent_tweets
from app.detector import classify
from app.push import build_reset_message, dispatch_to_all
from app.config import settings

logger = logging.getLogger(__name__)


def run_once() -> dict:
    """
    执行一次轮询：
    1. 拉取最新推文
    2. 过滤已处理 / 时间窗外
    3. 信号识别
    4. 推送 + 归档
    返回本次执行摘要。
    """
    summary = {
        "fetched": 0,
        "new_tweets": 0,
        "reset_detected": 0,
        "push_results": [],
    }

    tweets = fetch_recent_tweets(max_results=10)
    summary["fetched"] = len(tweets)

    if not tweets:
        logger.warning("No tweets fetched this round.")
        return summary

    last_poll = store.get_last_poll_time()
    now = datetime.utcnow()

    for tweet in tweets:
        # 跳过已处理
        if store.tweet_exists(tweet.tweet_id):
            continue

        # 时间窗过滤（仅处理 last_poll 之后的新推文）
        if last_poll and tweet.created_at <= last_poll:
            continue

        summary["new_tweets"] += 1
        result = classify(tweet.text)

        logger.info("Tweet %s | confidence=%s | is_reset=%s | text=%s",
                    tweet.tweet_id, result.confidence, result.is_reset,
                    tweet.text[:80])

        store.save_tweet(
            tweet_id=tweet.tweet_id,
            author=tweet.author,
            text=tweet.text,
            url=tweet.url,
            created_at=tweet.created_at,
            is_reset=result.is_reset,
            confidence=result.confidence,
        )

        if result.is_reset and result.confidence in ("high", "medium"):
            summary["reset_detected"] += 1
            event_id = store.create_reset_event(tweet.tweet_id, result.confidence)

            title, content = build_reset_message(tweet.text, tweet.url, result.confidence)

            subscriptions = store.get_all_subscriptions()
            push_results = dispatch_to_all(title, content, subscriptions)

            pushed_channels = [r.channel for r in push_results if r.success]
            store.mark_pushed(event_id, pushed_channels)

            summary["push_results"].extend([
                {"channel": r.channel, "success": r.success, "error": r.error}
                for r in push_results
            ])

            logger.info("Reset event %d pushed to %d channels.", event_id, len(pushed_channels))

    store.set_last_poll_time(now)
    return summary
