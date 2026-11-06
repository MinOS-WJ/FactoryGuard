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
from .dispatcher import DispatchReport, OutboxDispatcher
from .incidents import DeliveryResult, IncidentNotificationConfig, IncidentNotifier
from .providers import (
    EMAIL_PRESETS,
    SMS_PRESETS,
    WEBHOOK_PRESETS,
    email_preset,
    sms_preset,
    webhook_preset,
)
from .webhook import WebhookNotifier, WebhookRequest, build_webhook_request

__all__ = [
    "EMAIL_PRESETS",
    "SMS_PRESETS",
    "WEBHOOK_PRESETS",
    "ChannelDescriptor",
    "DeliveryResult",
    "DispatchReport",
    "EmailAdapter",
    "IncidentNotificationConfig",
    "IncidentNotifier",
    "NotificationAdapter",
    "NotificationDestination",
    "OutboxDispatcher",
    "SmsHttpAdapter",
    "WebhookAdapter",
    "WebhookNotifier",
    "WebhookRequest",
    "build_webhook_request",
    "channel_catalog",
    "channel_descriptor",
    "default_destination",
    "email_preset",
    "new_destination_id",
    "parse_phone_recipients",
    "parse_recipients",
    "sms_preset",
    "webhook_preset",
]
