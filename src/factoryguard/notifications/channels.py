"""Notification channel catalog and integration readiness metadata.

The catalog is deliberately provider-neutral.  It tells the control panel and
operators which channels have a built-in adapter and which ones still require
an official provider account/credential integration.  Secrets are never part
of the returned metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    """Public metadata for one notification channel."""

    key: str
    label: str
    adapter: str
    implemented: bool
    authorization_required: bool
    credential_fields: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["credential_fields"] = list(self.credential_fields)
        return value


_CHANNELS: tuple[ChannelDescriptor, ...] = (
    ChannelDescriptor(
        key="webhook",
        label="Webhook\uff08\u4f01\u4e1a\u5fae\u4fe1/\u5fae\u4fe1/\u9489\u9489/\u98de\u4e66/\u81ea\u5b9a\u4e49\uff09",
        adapter="https",
        implemented=True,
        authorization_required=True,
        credential_fields=("https_url", "signing_secret"),
        detail="\u5df2\u5b9e\u73b0\u901a\u7528 HTTPS \u9002\u914d\u5668\uff1b\u9875\u9762\u9884\u7559\u4f01\u4e1a\u5fae\u4fe1\u3001\u5fae\u4fe1\u3001\u9489\u9489\u3001\u98de\u4e66\u548c\u81ea\u5b9a\u4e49\u670d\u52a1\u5546\u5165\u53e3",
    ),
    ChannelDescriptor(
        key="email",
        label="\u90ae\u4ef6",
        adapter="smtp",
        implemented=True,
        authorization_required=True,
        credential_fields=("smtp_host", "smtp_username", "smtp_password_or_app_password"),
        detail="\u5df2\u5b9e\u73b0 SMTP\uff1b\u9884\u8bbe 163\u3001139\u3001QQ\u3001Outlook\u3001Google \u548c\u81ea\u5b9a\u4e49\u670d\u52a1\u5546",
    ),
    ChannelDescriptor(
        key="sms",
        label="\u77ed\u4fe1",
        adapter="provider_api",
        implemented=True,
        authorization_required=True,
        credential_fields=("provider", "access_key", "access_secret", "sign_name", "template_id"),
        detail="\u5df2\u5b9e\u73b0\u81ea\u5b9a\u4e49 HTTP \u77ed\u4fe1\u7f51\u5173\uff1b\u9884\u7559\u963f\u91cc\u4e91\u3001\u817e\u8baf\u4e91\u548c\u534e\u4e3a\u4e91\u4e13\u7528 API",
    ),
    ChannelDescriptor(
        key="wecom",
        label="\u4f01\u4e1a\u5fae\u4fe1",
        adapter="wecom_app_or_bot",
        implemented=False,
        authorization_required=True,
        credential_fields=("webhook_or_corp_id", "secret", "agent_id"),
        detail="\u9700\u8981\u4f01\u4e1a\u5fae\u4fe1\u7ba1\u7406\u5458\u521b\u5efa\u5e94\u7528\u6216\u673a\u5668\u4eba",
    ),
    ChannelDescriptor(
        key="dingtalk",
        label="\u9489\u9489",
        adapter="dingtalk_app_or_bot",
        implemented=False,
        authorization_required=True,
        credential_fields=("webhook_or_app_key", "app_secret_or_signing_secret"),
        detail="\u9700\u8981\u9489\u9489\u7ba1\u7406\u5458\u521b\u5efa\u673a\u5668\u4eba\u6216\u5e94\u7528",
    ),
    ChannelDescriptor(
        key="wechat",
        label="\u5fae\u4fe1",
        adapter="wechat_official_or_wecom_app",
        implemented=False,
        authorization_required=True,
        credential_fields=("app_id", "app_secret", "template_id"),
        detail="\u9884\u7559\u5fae\u4fe1\u670d\u52a1\u53f7\u6216\u4f01\u4e1a\u5fae\u4fe1\u5e94\u7528\u63a5\u5165\uff1b\u9700\u8981\u7ba1\u7406\u5458\u6388\u6743",
    ),
    ChannelDescriptor(
        key="feishu",
        label="\u98de\u4e66",
        adapter="feishu_bot",
        implemented=False,
        authorization_required=True,
        credential_fields=("webhook_url", "signing_secret"),
        detail="\u9884\u7559\u98de\u4e66\u673a\u5668\u4eba\u5165\u53e3\uff1b\u9700\u8981\u7fa4\u7ec4\u7ba1\u7406\u5458\u521b\u5efa\u673a\u5668\u4eba",
    ),
)


def channel_catalog() -> tuple[ChannelDescriptor, ...]:
    """Return immutable metadata for all supported channel types."""
    return _CHANNELS


def channel_descriptor(key: str) -> ChannelDescriptor:
    """Resolve one channel key, rejecting unknown configuration values."""
    normalized = str(key).strip().lower()
    for descriptor in _CHANNELS:
        if descriptor.key == normalized:
            return descriptor
    supported = ", ".join(item.key for item in _CHANNELS)
    raise ValueError(f"unsupported notification channel: {key!r}; supported: {supported}")


__all__ = ["ChannelDescriptor", "channel_catalog", "channel_descriptor"]
