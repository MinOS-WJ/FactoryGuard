"""Translate low-level media errors into operator-friendly Chinese text.

FFmpeg reports failures as multi-line English logs.  Store owners need a
short, actionable reason instead, while engineers keep the raw detail.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, str], ...] = (
    ("connection refused", "无法连接到摄像头，请检查设备是否开机、地址和端口是否正确。"),
    ("no route to host", "无法访问摄像头所在网络，请检查本机网络和设备 IP。"),
    ("network is unreachable", "无法访问摄像头所在网络，请检查本机网络连接。"),
    ("host is unreachable", "摄像头地址无法访问，请确认设备 IP 和网络互通情况。"),
    ("timed out", "连接摄像头超时，请检查网络是否通畅或设备是否离线。"),
    ("timeout", "连接摄像头超时，请检查网络是否通畅或设备是否离线。"),
    ("401 unauthorized", "账号或密码不正确，请重新填写设备账号和密码。"),
    ("unauthorized", "账号或密码不正确，请重新填写设备账号和密码。"),
    ("403 forbidden", "该账号没有查看权限，请换一个具备权限的账号。"),
    ("404 not found", "设备上没有这个视频地址，请核对 RTSP 路径是否正确。"),
    ("not found", "设备上没有这个视频地址，请核对 RTSP 路径是否正确。"),
    ("server returned 4", "设备拒绝了本次连接，请检查账号、权限或视频地址。"),
    ("invalid data found", "视频流格式不被支持，请改用主码流或子码流地址。"),
    ("could not find codec parameters", "无法解析视频流，请确认地址指向的是实时视频通道。"),
    ("no such file or directory", "视频地址不存在，请核对 RTSP 路径。"),
    ("permission denied", "没有访问权限，请检查设备账号和路径权限。"),
    ("authentication failed", "账号或密码不正确，请重新填写设备账号和密码。"),
    ("method not allowed", "设备不接受该请求方式，请核对视频地址。"),
)


def describe_media_error(detail: str) -> str:
    """Return a short Chinese reason for a media error, or an empty string."""
    text = " ".join(str(detail or "").split())
    if not text:
        return ""
    lowered = text.casefold()
    for needle, message in _PATTERNS:
        if needle in lowered:
            return message
    if "404" in lowered:
        return "设备上没有这个视频地址，请核对 RTSP 路径是否正确。"
    if "401" in lowered:
        return "账号或密码不正确，请重新填写设备账号和密码。"
    return ""


def summarise_media_error(detail: str, *, fallback: str = "") -> str:
    """Return an operator-facing message for a media error."""
    translated = describe_media_error(detail)
    if translated:
        return translated
    text = " ".join(str(detail or "").split())
    if not text:
        return fallback
    if len(text) > 160:
        text = text[:157] + "…"
    return fallback or text


def redact_media_credentials(value: str) -> str:
    """Remove any user info from a media URL before showing it to operators."""
    return re.sub(r"((?:rtsp|rtsps|https?)://)[^/@\s]+@", r"\1***:***@", value, flags=re.IGNORECASE)


__all__ = [
    "describe_media_error",
    "redact_media_credentials",
    "summarise_media_error",
]
