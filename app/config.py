"""
配置管理 - 从环境变量读取所有配置
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Twitter API v2
    twitter_bearer_token: Optional[str] = None  # Bearer Token，只读公开推文
    tibo_username: str = "thsottiaux"
    tibo_user_id: str = "1267893715938840576"  # @thsottiaux 的 user_id，避免每次查询
    poll_interval_min: int = 10

    # RSSHub（fallback，现在基本不可用）
    rsshub_base_url: str = "https://rsshub.app"

    # 数据库 - Render 免费版使用 /tmp（无持久卷时）
    store_path: str = "/tmp/codex_app.db"

    # Telegram
    tg_bot_token: Optional[str] = None
    tg_chat_id: Optional[str] = None

    # PushPlus 微信
    pushplus_token: Optional[str] = None
    pushplus_topic: Optional[str] = "codex-reset"

    # 企业微信机器人
    wecom_webhook: Optional[str] = None

    # Server酱
    serverchan_sendkey: Optional[str] = None

    # 短信
    sms_provider: Optional[str] = None
    aliyun_access_key_id: Optional[str] = None
    aliyun_access_key_secret: Optional[str] = None
    aliyun_sign_name: Optional[str] = None
    aliyun_template_code: Optional[str] = None
    tencent_secret_id: Optional[str] = None
    tencent_secret_key: Optional[str] = None
    tencent_sms_app_id: Optional[str] = None
    tencent_sms_sign: Optional[str] = None
    tencent_sms_template_id: Optional[str] = None

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    api_secret: str = "change-me-in-production"

    # 代理（国内部署时用于访问 X/RSSHub）
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
