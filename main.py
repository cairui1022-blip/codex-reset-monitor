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
