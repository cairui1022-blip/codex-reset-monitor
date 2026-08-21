"""
程序入口 - 启动 FastAPI + uvicorn
"""
import logging
import os
import subprocess
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

# 确保 Playwright Chromium 可用（Render 容器 build/run 分离，build 产物不自动持久化）
def _ensure_chromium():
    pw_path = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        "/opt/render/project/src/.playwright-browsers"
    )
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_path
    chromium_dir = os.path.join(pw_path, "chromium_headless_shell-1234")
    if not os.path.isdir(chromium_dir):
        logger.info("Chromium not found at %s, installing...", chromium_dir)
        try:
            result = subprocess.run(
                ["playwright", "install", "chromium", "--with-deps"],
                env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": pw_path},
                timeout=300,
                capture_output=False,
            )
            if result.returncode == 0:
                logger.info("Chromium installed successfully.")
            else:
                logger.warning("playwright install returned code %s", result.returncode)
        except Exception as e:
            logger.error("Failed to install Chromium: %s", e)
    else:
        logger.info("Chromium found at %s, skipping install.", chromium_dir)

_ensure_chromium()

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
