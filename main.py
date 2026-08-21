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

# 确保 Playwright Chromium 可用（Render 容器每次重启后 .playwright-browsers 为空）
def _ensure_chromium():
    pw_path = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        "/opt/render/project/src/.playwright-browsers"
    )
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_path

    # 检查目录是否存在且非空（有任何 chromium* 子目录）
    needs_install = True
    if os.path.isdir(pw_path):
        contents = [d for d in os.listdir(pw_path) if d.startswith("chromium")]
        if contents:
            needs_install = False
            logger.info("Chromium found at %s: %s", pw_path, contents)

    if needs_install:
        logger.info("Chromium not found at %s, running playwright install...", pw_path)
        os.makedirs(pw_path, exist_ok=True)
        try:
            result = subprocess.run(
                ["playwright", "install", "chromium", "--with-deps"],
                env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": pw_path},
                timeout=300,
                capture_output=False,
            )
            if result.returncode == 0:
                # 打印安装后目录内容
                try:
                    contents_after = os.listdir(pw_path)
                    logger.info("Chromium installed. Directory contents: %s", contents_after)
                except Exception:
                    pass
                logger.info("Chromium installed successfully.")
            else:
                logger.warning("playwright install returned code %s", result.returncode)
        except Exception as e:
            logger.error("Failed to install Chromium: %s", e)

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
