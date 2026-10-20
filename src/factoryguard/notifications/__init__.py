"""Notification channel adapters."""

from .adapters import (
    EmailAdapter,
    NotificationAdapter,
    SmsHttpAdapter,
    WebhookAdapter,
    parse_phone_recipients,
    parse_recipients,
)
from .channels import ChannelDescriptor, channel_catalog, channel_descriptor
from .destinations import NotificationDestination, default_destination, new_destination_id
from .providers import EMAIL_PRESETS, SMS_PRESETS, WEBHOOK_PRESETS, email_preset, sms_preset, webhook_preset
from .webhook import WebhookNotifier, WebhookRequest, build_webhook_request
from .dispatcher import DispatchReport, OutboxDispatcher

__all__ = [
    "ChannelDescriptor",
    "DispatchReport",
    "EmailAdapter",
    "EMAIL_PRESETS",
    "NotificationAdapter",
    "NotificationDestination",
    "OutboxDispatcher",
    "SMS_PRESETS",
    "SmsHttpAdapter",
    "WebhookAdapter",
    "WebhookNotifier",
    "WebhookRequest",
    "WEBHOOK_PRESETS",
    "build_webhook_request",
    "channel_catalog",
    "channel_descriptor",
    "default_destination",
    "email_preset",
    "parse_recipients",
    "parse_phone_recipients",
    "new_destination_id",
    "sms_preset",
    "webhook_preset",
]
