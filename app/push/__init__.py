"""
推送适配器层 - 统一接口 + 各通道实现
支持: PushPlus (微信), 企业微信机器人, Server酱, Telegram, 短信(阿里云/腾讯云)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    success: bool
    channel: str
    error: Optional[str] = None


# ─────────────────────────────────────────────
# 统一基类
# ─────────────────────────────────────────────

class PushChannel(ABC):
    name: str

    @abstractmethod
    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        """
        identifier: 个人 token/手机号/webhook 等；
                    空字符串时使用全局配置（系统级推送）。
        """
        ...

    def _post_json(self, url: str, payload: dict, timeout: int = 10) -> requests.Response:
        proxies = None
        if settings.http_proxy or settings.https_proxy:
            proxies = {
                "http": settings.http_proxy or settings.https_proxy,
                "https": settings.https_proxy or settings.http_proxy,
            }
        return requests.post(url, json=payload, timeout=timeout, proxies=proxies)


# ─────────────────────────────────────────────
# PushPlus 微信（主推）
# ─────────────────────────────────────────────

class PushPlusChannel(PushChannel):
    name = "pushplus"
    _API = "https://www.pushplus.plus/send"

    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        token = identifier or settings.pushplus_token
        if not token:
            return SendResult(success=False, channel=self.name, error="No PushPlus token")

        payload = {
            "token": token,
            "title": title,
            "content": content,
            "template": "html",
        }
        # 如果是系统级 token 且配置了 topic，使用群推
        if not identifier and settings.pushplus_topic:
            payload["topic"] = settings.pushplus_topic

        try:
            resp = self._post_json(self._API, payload)
            data = resp.json()
            if data.get("code") == 200:
                return SendResult(success=True, channel=self.name)
            return SendResult(success=False, channel=self.name,
                              error=data.get("msg", str(data)))
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))


# ─────────────────────────────────────────────
# 企业微信群机器人
# ─────────────────────────────────────────────

class WeChatWorkChannel(PushChannel):
    name = "wecom"

    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        webhook = identifier or settings.wecom_webhook
        if not webhook:
            return SendResult(success=False, channel=self.name, error="No WeChat Work webhook")

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n{content}"
            }
        }
        try:
            resp = self._post_json(webhook, payload)
            data = resp.json()
            if data.get("errcode") == 0:
                return SendResult(success=True, channel=self.name)
            return SendResult(success=False, channel=self.name,
                              error=data.get("errmsg", str(data)))
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))


# ─────────────────────────────────────────────
# Server酱 Turbo（兜底）
# ─────────────────────────────────────────────

class ServerChanChannel(PushChannel):
    name = "serverchan"

    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        sendkey = identifier or settings.serverchan_sendkey
        if not sendkey:
            return SendResult(success=False, channel=self.name, error="No Server酱 sendkey")

        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        try:
            resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
            data = resp.json()
            if data.get("errno") == 0 or data.get("code") == 0:
                return SendResult(success=True, channel=self.name)
            return SendResult(success=False, channel=self.name,
                              error=data.get("message", str(data)))
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))


# ─────────────────────────────────────────────
# Telegram Bot
# ─────────────────────────────────────────────

class TelegramChannel(PushChannel):
    name = "telegram"
    _API_TMPL = "https://api.telegram.org/bot{token}/sendMessage"

    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        bot_token = settings.tg_bot_token
        chat_id = identifier or settings.tg_chat_id
        if not bot_token or not chat_id:
            return SendResult(success=False, channel=self.name,
                              error="Missing TG_BOT_TOKEN or chat_id")

        text = f"*{title}*\n\n{content}"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        proxies = None
        if settings.http_proxy or settings.https_proxy:
            proxies = {
                "http": settings.http_proxy or settings.https_proxy,
                "https": settings.https_proxy or settings.http_proxy,
            }
        try:
            url = self._API_TMPL.format(token=bot_token)
            resp = requests.post(url, json=payload, timeout=10, proxies=proxies)
            data = resp.json()
            if data.get("ok"):
                return SendResult(success=True, channel=self.name)
            return SendResult(success=False, channel=self.name,
                              error=data.get("description", str(data)))
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))


# ─────────────────────────────────────────────
# 短信 - 阿里云
# ─────────────────────────────────────────────

class AliyunSMSChannel(PushChannel):
    name = "sms_aliyun"

    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        """identifier = 手机号 (11位国内号码)"""
        phone = identifier
        if not phone:
            return SendResult(success=False, channel=self.name, error="No phone number")

        required = [
            settings.aliyun_access_key_id,
            settings.aliyun_access_key_secret,
            settings.aliyun_sign_name,
            settings.aliyun_template_code,
        ]
        if not all(required):
            return SendResult(success=False, channel=self.name,
                              error="Aliyun SMS config incomplete")

        # 使用阿里云 OpenAPI SDK（简化版 HTTP 请求）
        try:
            from alibabacloud_dysmsapi20170525.client import Client
            from alibabacloud_dysmsapi20170525 import models as sms_models
            from alibabacloud_tea_openapi import models as open_api_models

            config = open_api_models.Config(
                access_key_id=settings.aliyun_access_key_id,
                access_key_secret=settings.aliyun_access_key_secret,
            )
            config.endpoint = "dysmsapi.aliyuncs.com"
            client = Client(config)

            req = sms_models.SendSmsRequest(
                phone_numbers=phone,
                sign_name=settings.aliyun_sign_name,
                template_code=settings.aliyun_template_code,
                template_param=json.dumps({"content": "Codex额度已重置，请前往使用！"}),
            )
            resp = client.send_sms(req)
            if resp.body.code == "OK":
                return SendResult(success=True, channel=self.name)
            return SendResult(success=False, channel=self.name,
                              error=f"{resp.body.code}: {resp.body.message}")
        except ImportError:
            # SDK 未安装时，使用简单 HTTP 接口
            return self._send_via_http(phone)
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))

    def _send_via_http(self, phone: str) -> SendResult:
        """阿里云短信 HTTP 方式（不依赖 SDK）"""
        import base64
        import urllib.parse
        from datetime import datetime

        def percent_encode(s: str) -> str:
            return urllib.parse.quote(str(s), safe="~")

        def sign(key: str, msg: str) -> str:
            import hmac, hashlib, base64
            h = hmac.new((key + "&").encode("utf-8"), msg.encode("utf-8"), hashlib.sha1)
            return base64.b64encode(h.digest()).decode()

        params = {
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(int(time.time() * 1000)),
            "AccessKeyId": settings.aliyun_access_key_id,
            "SignatureVersion": "1.0",
            "Timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Format": "JSON",
            "Action": "SendSms",
            "Version": "2017-05-25",
            "RegionId": "cn-hangzhou",
            "PhoneNumbers": phone,
            "SignName": settings.aliyun_sign_name,
            "TemplateCode": settings.aliyun_template_code,
            "TemplateParam": json.dumps({"content": "Codex额度已重置"}, ensure_ascii=False),
        }

        sorted_params = sorted(params.items())
        query_string = "&".join(f"{percent_encode(k)}={percent_encode(v)}"
                                for k, v in sorted_params)
        string_to_sign = f"POST&{percent_encode('/')}&{percent_encode(query_string)}"
        signature = sign(settings.aliyun_access_key_secret, string_to_sign)
        params["Signature"] = signature

        try:
            resp = requests.post(
                "https://dysmsapi.aliyuncs.com/",
                data=params, timeout=10
            )
            data = resp.json()
            if data.get("Code") == "OK":
                return SendResult(success=True, channel=self.name)
            return SendResult(success=False, channel=self.name,
                              error=f"{data.get('Code')}: {data.get('Message')}")
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))


# ─────────────────────────────────────────────
# 短信 - 腾讯云
# ─────────────────────────────────────────────

class TencentSMSChannel(PushChannel):
    name = "sms_tencent"

    def send(self, title: str, content: str, identifier: str = "") -> SendResult:
        phone = identifier
        if not phone:
            return SendResult(success=False, channel=self.name, error="No phone number")

        required = [
            settings.tencent_secret_id,
            settings.tencent_secret_key,
            settings.tencent_sms_app_id,
            settings.tencent_sms_sign,
            settings.tencent_sms_template_id,
        ]
        if not all(required):
            return SendResult(success=False, channel=self.name,
                              error="Tencent SMS config incomplete")

        try:
            from tencentcloud.common import credential
            from tencentcloud.sms.v20210111 import sms_client, models as tc_models

            cred = credential.Credential(settings.tencent_secret_id, settings.tencent_secret_key)
            client = sms_client.SmsClient(cred, "ap-guangzhou")

            req = tc_models.SendSmsRequest()
            req.SmsSdkAppId = settings.tencent_sms_app_id
            req.SignName = settings.tencent_sms_sign
            req.TemplateId = settings.tencent_sms_template_id
            req.TemplateParamSet = ["Codex额度已重置，请前往使用！"]
            req.PhoneNumberSet = [f"+86{phone}"]

            resp = client.SendSms(req)
            if resp.SendStatusSet and resp.SendStatusSet[0].Code == "Ok":
                return SendResult(success=True, channel=self.name)
            return SendResult(
                success=False, channel=self.name,
                error=resp.SendStatusSet[0].Message if resp.SendStatusSet else "Unknown error"
            )
        except ImportError:
            return SendResult(success=False, channel=self.name,
                              error="tencentcloud SDK not installed")
        except Exception as e:
            return SendResult(success=False, channel=self.name, error=str(e))


# ─────────────────────────────────────────────
# 推送引擎 - 并发 fan-out
# ─────────────────────────────────────────────

def build_reset_message(tweet_text: str, tweet_url: str, confidence: str) -> tuple[str, str]:
    """构建推送标题与内容（HTML格式）"""
    title = "🔄 Codex 额度已重置！"
    if confidence == "medium":
        title = "⚠️ Codex 可能重置额度（待确认）"

    content = f"""<b>Codex 额度重置提醒</b><br><br>
Tibo（@thsottiaux）刚刚发推，Codex 使用额度已重置！<br><br>
<b>原文：</b><br>
{tweet_text}<br><br>
<a href="{tweet_url}">点击查看原推</a><br><br>
<i>置信度：{confidence}</i>
"""
    return title, content


def dispatch_to_all(title: str, content: str,
                    subscriptions: list) -> list[SendResult]:
    """
    并发推送到所有已启用的订阅者。
    subscriptions: list of Subscription ORM objects
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pushplus = PushPlusChannel()
    wecom = WeChatWorkChannel()
    serverchan = ServerChanChannel()
    telegram = TelegramChannel()
    aliyun_sms = AliyunSMSChannel()
    tencent_sms = TencentSMSChannel()

    channel_map = {
        "pushplus": pushplus,
        "wecom": wecom,
        "serverchan": serverchan,
        "telegram": telegram,
        "sms_aliyun": aliyun_sms,
        "sms_tencent": tencent_sms,
        # phone 渠道根据 SMS 配置路由
        "phone": aliyun_sms if settings.sms_provider == "aliyun" else tencent_sms,
    }

    # 系统级推送（全局 token/webhook）
    system_tasks = []
    if settings.pushplus_token:
        system_tasks.append(("pushplus", pushplus, ""))
    if settings.wecom_webhook:
        system_tasks.append(("wecom", wecom, ""))
    if settings.tg_bot_token and settings.tg_chat_id:
        system_tasks.append(("telegram", telegram, ""))
    if settings.serverchan_sendkey:
        system_tasks.append(("serverchan", serverchan, ""))

    # 用户个人订阅推送
    user_tasks = []
    for sub in subscriptions:
        ch_name = sub.channel
        if ch_name in channel_map:
            user_tasks.append((ch_name, channel_map[ch_name], sub.identifier))

    all_tasks = system_tasks + user_tasks
    results = []

    def _send(task):
        ch_name, channel, identifier = task
        # 指数退避重试
        for attempt in range(3):
            result = channel.send(title, content, identifier)
            if result.success:
                return result
            wait = 2 ** attempt
            logger.warning("Push failed [%s] attempt %d: %s, retrying in %ds",
                           ch_name, attempt + 1, result.error, wait)
            time.sleep(wait)
        return result

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_send, task): task for task in all_tasks}
        for future in as_completed(futures):
            try:
                res = future.result(timeout=30)
                results.append(res)
                if res.success:
                    logger.info("Push succeeded: %s", res.channel)
                else:
                    logger.error("Push failed: %s - %s", res.channel, res.error)
            except Exception as e:
                ch_name = futures[future][0]
                logger.error("Push exception [%s]: %s", ch_name, e)
                results.append(SendResult(success=False, channel=ch_name, error=str(e)))

    return results
