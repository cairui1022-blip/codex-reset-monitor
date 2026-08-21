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


def _ensure_chromium():
    """
    确保 Playwright Chromium 可用。
    
    Render.com 的 build 容器和 run 容器是完全独立的文件系统，
    因此 playwright install 必须在 **运行时** 执行，安装到 /tmp（运行时可写目录）。
    
    /tmp 在同一容器实例的生命周期内持久化，重启后重新安装（通常 <3 min）。
    """
    # 使用 /tmp 作为 Playwright browsers 路径（运行时可写，build 容器隔离不影响）
    pw_path = "/tmp/pw-browsers"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_path
    logger.info("PLAYWRIGHT_BROWSERS_PATH set to %s", pw_path)

    # 检查是否已安装
    needs_install = True
    if os.path.isdir(pw_path):
        chromium_dirs = [d for d in os.listdir(pw_path) if d.startswith("chromium")]
        if chromium_dirs:
            needs_install = False
            logger.info("Chromium already installed: %s", chromium_dirs)

    if needs_install:
        logger.info("Installing Playwright Chromium to %s ...", pw_path)
        os.makedirs(pw_path, exist_ok=True)
        try:
            result = subprocess.run(
                ["playwright", "install", "chromium", "--with-deps"],
                env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": pw_path},
                timeout=300,
                check=False,
            )
            if result.returncode == 0:
                contents = os.listdir(pw_path)
                logger.info("Chromium installed. Directory: %s", contents)
            else:
                logger.error("playwright install returned code %s", result.returncode)
        except Exception as e:
            logger.error("Failed to install Chromium: %s", e)
    else:
        logger.info("Chromium already available, skipping install.")


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
