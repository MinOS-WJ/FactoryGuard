"""Provider presets used by notification configuration pages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebhookProviderPreset:
    key: str
    label: str
    url_hint: str
    secret_label: str
    detail: str


@dataclass(frozen=True, slots=True)
class EmailProviderPreset:
    key: str
    label: str
    host: str
    port: int
    security: str
    detail: str


@dataclass(frozen=True, slots=True)
class SmsProviderPreset:
    key: str
    label: str
    implemented: bool
    endpoint_hint: str
    detail: str


WEBHOOK_PRESETS: tuple[WebhookProviderPreset, ...] = (
    WebhookProviderPreset(
        "wecom",
        "企业微信群机器人",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
        "接收端签名密钥（可选预留）",
        "预留企业微信消息模板；当前统一通过 HTTPS Webhook 适配器投递。",
    ),
    WebhookProviderPreset(
        "wechat",
        "微信服务号 / 企业微信应用",
        "https://example.com/wechat/notification",
        "应用 Secret（仅当前进程）",
        "预留微信服务号或企业微信应用接入；需要公众号/应用管理员授权。",
    ),
    WebhookProviderPreset(
        "dingtalk",
        "钉钉机器人",
        "https://oapi.dingtalk.com/robot/send?access_token=...",
        "加签 Secret（仅当前进程）",
        "预留钉钉机器人消息模板与加签参数；当前走通用 Webhook 请求。",
    ),
    WebhookProviderPreset(
        "feishu",
        "飞书机器人",
        "https://open.feishu.cn/open-apis/bot/v2/hook/...",
        "签名校验 Secret（仅当前进程）",
        "预留飞书机器人消息模板与签名参数；当前走通用 Webhook 请求。",
    ),
    WebhookProviderPreset(
        "custom",
        "自定义 HTTPS Webhook",
        "https://example.com/factoryguard/webhook",
        "HMAC 签名密钥（仅当前进程）",
        "使用 FactoryGuard 标准事件 JSON 和 HMAC-SHA256 签名。",
    ),
)

EMAIL_PRESETS: tuple[EmailProviderPreset, ...] = (
    EmailProviderPreset("163", "网易 163 邮箱", "smtp.163.com", 465, "ssl", "使用客户端授权码，不要填写网页登录密码。"),
    EmailProviderPreset("139", "中国移动 139 邮箱", "smtp.139.com", 465, "ssl", "通常需要在邮箱设置中开启 SMTP 并使用授权码。"),
    EmailProviderPreset("qq", "QQ 邮箱", "smtp.qq.com", 465, "ssl", "使用 QQ 邮箱生成的 SMTP 授权码。"),
    EmailProviderPreset("outlook", "Outlook", "smtp-mail.outlook.com", 587, "starttls", "Microsoft 账号可能需要应用密码或 OAuth 策略支持。"),
    EmailProviderPreset("google", "Google Gmail", "smtp.gmail.com", 587, "starttls", "建议启用两步验证并生成应用专用密码。"),
    EmailProviderPreset("custom", "自定义 SMTP", "", 587, "starttls", "手动填写 SMTP 主机、端口和加密方式。"),
)

SMS_PRESETS: tuple[SmsProviderPreset, ...] = (
    SmsProviderPreset(
        "custom_http",
        "自定义 HTTP 短信网关",
        True,
        "https://sms-gateway.example.com/send",
        "发送标准 JSON 请求；适合企业自有短信网关或内部转发服务。",
    ),
    SmsProviderPreset(
        "aliyun",
        "阿里云短信（预留）",
        False,
        "https://dysmsapi.aliyuncs.com/",
        "预留阿里云短信 API；正式接入需要 AccessKey、签名和模板审核。",
    ),
    SmsProviderPreset(
        "tencent",
        "腾讯云短信（预留）",
        False,
        "https://sms.tencentcloudapi.com/",
        "预留腾讯云短信 API；正式接入需要 SecretId、SecretKey、SDKAppID 和模板。",
    ),
    SmsProviderPreset(
        "huawei",
        "华为云短信（预留）",
        False,
        "https://smsapi.cn-north-4.myhuaweicloud.com/",
        "预留华为云短信 API；正式接入需要 AppKey、AppSecret、签名和模板。",
    ),
)


def webhook_preset(key: str) -> WebhookProviderPreset:
    return _find(WEBHOOK_PRESETS, key, "custom")


def email_preset(key: str) -> EmailProviderPreset:
    return _find(EMAIL_PRESETS, key, "custom")


def sms_preset(key: str) -> SmsProviderPreset:
    return _find(SMS_PRESETS, key, "custom_http")


def _find(values, key: str, default: str):
    normalized = str(key).strip().lower()
    for value in values:
        if value.key == normalized:
            return value
    for value in values:
        if value.key == default:
            return value
    raise ValueError(f"provider preset default is missing: {default}")


__all__ = [
    "EMAIL_PRESETS",
    "SMS_PRESETS",
    "WEBHOOK_PRESETS",
    "EmailProviderPreset",
    "SmsProviderPreset",
    "WebhookProviderPreset",
    "email_preset",
    "sms_preset",
    "webhook_preset",
]
