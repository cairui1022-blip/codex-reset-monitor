"""
数据库模型与存储层 - SQLite via SQLAlchemy
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    create_engine, Column, String, Integer, Float, Text,
    DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from contextlib import contextmanager
import json
import os

Base = declarative_base()


class TweetRecord(Base):
    __tablename__ = "tweet_record"

    tweet_id = Column(String, primary_key=True)
    author = Column(String, nullable=False)
    text = Column(Text)
    url = Column(String)
    created_at = Column(DateTime)
    processed = Column(Boolean, default=False)
    is_reset = Column(Boolean, default=False)
    confidence = Column(String)          # high / medium / low
    fetched_at = Column(DateTime, default=datetime.utcnow)


class ResetEvent(Base):
    __tablename__ = "reset_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tweet_id = Column(String, ForeignKey("tweet_record.tweet_id"))
    detected_at = Column(DateTime, default=datetime.utcnow)
    confidence = Column(String)
    pushed_channels = Column(Text, default="[]")   # JSON list
    pushed_at = Column(DateTime)


class Subscription(Base):
    __tablename__ = "subscription"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String)             # pushplus / phone / wecom / telegram
    identifier = Column(String)          # token / phone / webhook / chat_id
    label = Column(String)               # 备注（如用户昵称）
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PollerState(Base):
    __tablename__ = "poller_state"

    key = Column(String, primary_key=True)
    value = Column(Text)


# ─────────────────────────────────────────────
# DB 初始化
# ─────────────────────────────────────────────

_engine = None
_SessionFactory = None


def init_db(db_path: str):
    global _engine, _SessionFactory
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine)


@contextmanager
def get_session() -> Session:
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ─────────────────────────────────────────────
# 业务操作
# ─────────────────────────────────────────────

def tweet_exists(tweet_id: str) -> bool:
    with get_session() as s:
        return s.query(TweetRecord).filter_by(tweet_id=tweet_id).first() is not None


def save_tweet(tweet_id: str, author: str, text: str, url: str,
               created_at: datetime, is_reset: bool = False,
               confidence: str = "low") -> None:
    with get_session() as s:
        rec = TweetRecord(
            tweet_id=tweet_id,
            author=author,
            text=text,
            url=url,
            created_at=created_at,
            processed=True,
            is_reset=is_reset,
            confidence=confidence,
        )
        s.merge(rec)


def create_reset_event(tweet_id: str, confidence: str) -> int:
    with get_session() as s:
        ev = ResetEvent(
            tweet_id=tweet_id,
            detected_at=datetime.utcnow(),
            confidence=confidence,
            pushed_channels="[]",
        )
        s.add(ev)
        s.flush()
        return ev.id


def mark_pushed(event_id: int, channels: list[str]) -> None:
    with get_session() as s:
        ev = s.query(ResetEvent).filter_by(id=event_id).first()
        if ev:
            ev.pushed_channels = json.dumps(channels)
            ev.pushed_at = datetime.utcnow()


def get_last_poll_time() -> Optional[datetime]:
    with get_session() as s:
        row = s.query(PollerState).filter_by(key="last_poll").first()
        if row and row.value:
            return datetime.fromisoformat(row.value)
    return None


def set_last_poll_time(dt: datetime) -> None:
    with get_session() as s:
        row = s.query(PollerState).filter_by(key="last_poll").first()
        if row:
            row.value = dt.isoformat()
        else:
            s.add(PollerState(key="last_poll", value=dt.isoformat()))


def get_all_subscriptions(channel: Optional[str] = None) -> list[Subscription]:
    with get_session() as s:
        q = s.query(Subscription).filter_by(enabled=True)
        if channel:
            q = q.filter_by(channel=channel)
        results = q.all()
        # detach from session
        s.expunge_all()
        return results


def add_subscription(channel: str, identifier: str, label: str = "") -> int:
    with get_session() as s:
        # 检查是否已存在
        existing = s.query(Subscription).filter_by(
            channel=channel, identifier=identifier
        ).first()
        if existing:
            existing.enabled = True
            return existing.id
        sub = Subscription(channel=channel, identifier=identifier, label=label)
        s.add(sub)
        s.flush()
        return sub.id


def remove_subscription(sub_id: int) -> bool:
    with get_session() as s:
        sub = s.query(Subscription).filter_by(id=sub_id).first()
        if sub:
            sub.enabled = False
            return True
    return False


def get_reset_stats() -> dict:
    """计算统计指标"""
    with get_session() as s:
        events = s.query(ResetEvent).order_by(ResetEvent.detected_at).all()
        total = len(events)
        if total == 0:
            return {"total_resets": 0, "avg_interval_days": None,
                    "longest_wait_days": None, "latest_reset": None}

        latest = events[-1].detected_at

        if total >= 2:
            intervals = []
            for i in range(1, len(events)):
                delta = (events[i].detected_at - events[i - 1].detected_at).total_seconds() / 86400
                intervals.append(delta)
            avg_interval = round(sum(intervals) / len(intervals), 1)
            longest_wait = round(max(intervals), 1)
        else:
            avg_interval = None
            longest_wait = None

        return {
            "total_resets": total,
            "avg_interval_days": avg_interval,
            "longest_wait_days": longest_wait,
            "latest_reset": latest.isoformat() + "Z",
        }


def get_reset_history(limit: int = 20) -> list[dict]:
    with get_session() as s:
        events = (
            s.query(ResetEvent, TweetRecord)
            .join(TweetRecord, ResetEvent.tweet_id == TweetRecord.tweet_id)
            .order_by(ResetEvent.detected_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": ev.id,
                "detected_at": ev.detected_at.isoformat() + "Z",
                "confidence": ev.confidence,
                "tweet_id": tw.tweet_id,
                "tweet_url": tw.url,
                "tweet_text": tw.text,
                "pushed_channels": json.loads(ev.pushed_channels or "[]"),
            }
            for ev, tw in events
        ]
