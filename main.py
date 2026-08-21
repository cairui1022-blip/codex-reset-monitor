"""
程序入口 - 启动 FastAPI + uvicorn
"""
import logging
import os
import sys

import uvicorn

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

def _log_chromium_state():
    """启动时打印 Playwright 浏览器目录状态，便于诊断。"""
    pw_path = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        "/opt/render/project/src/.playwright-browsers"
    )
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_path
    logger.info("PLAYWRIGHT_BROWSERS_PATH = %s", pw_path)
    if os.path.isdir(pw_path):
        try:
            contents = os.listdir(pw_path)
            logger.info("Browser dir contents: %s", contents)
            # Walk first 2 levels to find executable
            for entry in contents:
                sub = os.path.join(pw_path, entry)
                if os.path.isdir(sub):
                    try:
                        sub_contents = os.listdir(sub)
                        logger.info("  %s/ -> %s", entry, sub_contents)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("Cannot list browser dir: %s", e)
    else:
        logger.warning("Browser dir does NOT exist: %s", pw_path)
        # Fallback: check default playwright cache
        import subprocess
        try:
            r = subprocess.run(
                ["playwright", "install", "--dry-run", "chromium"],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": pw_path},
            )
            logger.info("dry-run stdout: %s", r.stdout[:500])
            logger.info("dry-run stderr: %s", r.stderr[:500])
        except Exception as ex:
            logger.warning("dry-run failed: %s", ex)

_log_chromium_state()

from app.api import app
from app.config import settings

if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.web_port))
    uvicorn.run(
        "main:app",
        host=settings.web_host,
        port=port,
        log_level="info",
        reload=False,
    )
