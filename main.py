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

# Playwright browsers 安装在 /tmp/pw-browsers
# (由 startCommand 中的 playwright install 在 Python 启动前完成)
pw_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/tmp/pw-browsers")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw_path
logger.info("PLAYWRIGHT_BROWSERS_PATH = %s", pw_path)
if os.path.isdir(pw_path):
    logger.info("Browser dir contents: %s", os.listdir(pw_path))
else:
    logger.warning("Browser dir not found: %s (playwright install may have failed)", pw_path)

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
