"""
FastAPI 主应用 - REST API + 订阅管理 + 静态前端
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime

import qrcode
from fastapi import FastAPI, HTTPException, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, validator

from app import store
from app.config import settings
from app.scheduler import run_once

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 生命周期 - 启动时初始化 DB + 调度器
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db(settings.store_path)
    logger.info("DB initialized at %s", settings.store_path)

    # 启动后台轮询线程
    _start_scheduler_thread()
    yield


def _start_scheduler_thread():
    import schedule, time

    def _job():
        try:
            summary = run_once()
            logger.info("Poll done: %s", summary)
        except Exception as e:
            logger.exception("Scheduler error: %s", e)

    def _runner():
        schedule.every(settings.poll_interval_min).minutes.do(_job)
        # 启动时立即执行一次
        _job()
        while True:
            schedule.run_pending()
            time.sleep(30)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    logger.info("Scheduler started (interval=%d min)", settings.poll_interval_min)


# ─────────────────────────────────────────────
# App 实例
# ─────────────────────────────────────────────

app = FastAPI(
    title="Codex Reset Monitor",
    description="监控 Tibo 推特，第一时间推送 Codex 额度重置提醒",
    version="1.0.0",
    lifespan=lifespan,
)

# 模板路径 - 基于项目根目录（启动时 cwd = 项目根）
_tmpl_dir = os.path.join(os.getcwd(), "app", "web", "templates")
if not os.path.isdir(_tmpl_dir):
    # fallback: 相对本文件的上上级
    _tmpl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "templates")
templates = Jinja2Templates(directory=_tmpl_dir)


# ─────────────────────────────────────────────
# 请求/响应模型
# ─────────────────────────────────────────────

class SubscribePhoneRequest(BaseModel):
    phone: str

    @validator("phone")
    def validate_phone(cls, v):
        v = v.strip()
        if not re.fullmatch(r"1[3-9]\d{9}", v):
            raise ValueError("请输入有效的11位手机号")
        return v


class SubscribePushPlusRequest(BaseModel):
    token: str
    label: str = ""

    @validator("token")
    def validate_token(cls, v):
        v = v.strip()
        if len(v) < 10:
            raise ValueError("PushPlus token 长度不足")
        return v


class UnsubscribeRequest(BaseModel):
    sub_id: int


# ─────────────────────────────────────────────
# REST API
# ─────────────────────────────────────────────

@app.get("/api/v1/status")
def api_status():
    """获取最新重置状态与统计"""
    stats = store.get_reset_stats()
    days_since = None
    if stats.get("latest_reset"):
        latest_dt = datetime.fromisoformat(stats["latest_reset"].rstrip("Z"))
        days_since = round((datetime.utcnow() - latest_dt).total_seconds() / 86400, 1)
    return {
        **stats,
        "days_since_last_reset": days_since,
    }


@app.get("/api/v1/resets")
def api_resets(limit: int = 20):
    """重置历史列表"""
    return {"resets": store.get_reset_history(limit=min(limit, 100))}


@app.get("/api/v1/resets/latest")
def api_latest_reset():
    """最近一次重置详情"""
    history = store.get_reset_history(limit=1)
    if not history:
        raise HTTPException(status_code=404, detail="暂无重置记录")
    return history[0]


@app.post("/api/v1/subscribe/pushplus")
def subscribe_pushplus(req: SubscribePushPlusRequest):
    """用户填写 PushPlus token 订阅微信提醒"""
    sub_id = store.add_subscription(
        channel="pushplus",
        identifier=req.token,
        label=req.label,
    )
    return {"success": True, "sub_id": sub_id, "message": "订阅成功！重置时将通过微信推送提醒。"}


@app.post("/api/v1/subscribe/phone")
def subscribe_phone(req: SubscribePhoneRequest):
    """用户填写手机号订阅短信提醒"""
    if not settings.sms_provider:
        raise HTTPException(status_code=503, detail="短信功能暂未启用，请联系管理员配置")
    sub_id = store.add_subscription(
        channel="phone",
        identifier=req.phone,
        label="",
    )
    return {"success": True, "sub_id": sub_id, "message": f"手机号 {req.phone[:3]}****{req.phone[-4:]} 订阅成功！"}


@app.delete("/api/v1/subscribe/{sub_id}")
def unsubscribe(sub_id: int):
    """取消订阅"""
    ok = store.remove_subscription(sub_id)
    if not ok:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"success": True, "message": "已取消订阅"}


@app.post("/api/v1/admin/poll")
def admin_poll(request: Request):
    """手动触发一次轮询（管理接口）"""
    secret = request.headers.get("X-Admin-Secret", "")
    if secret != settings.api_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        summary = run_once()
        return {"success": True, "summary": summary}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# PushPlus 二维码生成
# ─────────────────────────────────────────────

@app.get("/api/v1/pushplus/qrcode")
def pushplus_qrcode():
    """
    返回 PushPlus 登录页二维码图片（PNG）。
    用户扫码后在 PushPlus 官网获取 token，再填写到订阅页面。
    """
    pushplus_url = "https://www.pushplus.plus/"
    img = qrcode.make(pushplus_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# ─────────────────────────────────────────────
# 前端页面
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    stats = store.get_reset_stats()
    days_since = None
    if stats.get("latest_reset"):
        latest_dt = datetime.fromisoformat(stats["latest_reset"].rstrip("Z"))
        days_since = round((datetime.utcnow() - latest_dt).total_seconds() / 86400, 1)
    history = store.get_reset_history(limit=50)
    # 计算每次重置距今天数，添加人类友好时间
    now = datetime.utcnow()
    for item in history:
        dt = datetime.fromisoformat(item["detected_at"].rstrip("Z"))
        delta = now - dt
        d = delta.days
        if d == 0:
            item["time_ago"] = "今天"
        elif d == 1:
            item["time_ago"] = "昨天"
        elif d < 30:
            item["time_ago"] = f"{d} 天前"
        elif d < 365:
            item["time_ago"] = f"{d // 30} 个月前"
        else:
            item["time_ago"] = f"{d // 365} 年前"
        item["date_str"] = dt.strftime("%Y-%m-%d %H:%M")

    # 构建热力图数据：{"YYYY-MM-DD": count, "YYYY-MM-DD_s": "推文摘要"}
    heatmap_dates: dict = {}
    for item in history:
        try:
            dt = datetime.fromisoformat(item["detected_at"].rstrip("Z"))
            date_key = dt.strftime("%Y-%m-%d")
            heatmap_dates[date_key] = heatmap_dates.get(date_key, 0) + 1
            # 只保留第一条推文摘要作为 tooltip
            snippet_key = date_key + "_s"
            if snippet_key not in heatmap_dates:
                raw = item.get("tweet_text") or ""
                heatmap_dates[snippet_key] = raw[:80] + ("…" if len(raw) > 80 else "")
        except Exception:
            pass

    return templates.TemplateResponse("index.html", {
        "request": request,
        "stats": stats,
        "days_since": days_since,
        "sms_enabled": bool(settings.sms_provider),
        "history": history,
        "heatmap_dates": heatmap_dates,
    })


@app.get("/api/v1/debug/collector")
def debug_collector(request: Request):
    """诊断 curl_cffi GraphQL 采集器连通性（需要 X-Admin-Secret header）"""
    secret = request.headers.get("X-Admin-Secret", "")
    if secret != settings.api_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.collector import debug_fetch
    return debug_fetch(max_results=5)


@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}
