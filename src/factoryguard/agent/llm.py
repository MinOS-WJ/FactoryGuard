"""Small OpenAI-compatible LLM client used by the local console.

The product intentionally keeps a deterministic offline assistant.  This
adapter is optional and only handles generation; incident creation, access
control and data retrieval remain local.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from factoryguard.net import read_http_error


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LLMConfig:
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    timeout: float = 35.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("文字问询超时时间必须为正数")
        endpoint = self.endpoint.strip()
        if endpoint and not endpoint.lower().startswith(("http://", "https://")):
            raise ValueError("文字问询接口地址必须以 http:// 或 https:// 开头")

    @property
    def available(self) -> bool:
        return bool(
            self.enabled and self.endpoint.strip() and self.model.strip() and self.api_key.strip()
        )

    @property
    def display_name(self) -> str:
        return self.model.strip() or "未配置文字问询服务"

    @classmethod
    def from_environment(cls) -> LLMConfig:
        endpoint = os.environ.get("FACTORYGUARD_LLM_ENDPOINT", "").strip()
        model = os.environ.get("FACTORYGUARD_LLM_MODEL", "").strip()
        api_key = (
            os.environ.get("FACTORYGUARD_LLM_API_KEY", "").strip()
            or os.environ.get("OPENAI_API_KEY", "").strip()
        )
        timeout = _positive_float(os.environ.get("FACTORYGUARD_LLM_TIMEOUT"), 35.0)
        return cls(endpoint=endpoint, model=model, api_key=api_key, timeout=timeout)

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any], *, api_key: str = "") -> LLMConfig:
        return cls(
            endpoint=str(settings.get("endpoint", "")).strip(),
            model=str(settings.get("model", "")).strip(),
            api_key=api_key.strip(),
            timeout=_positive_float(settings.get("timeout"), 35.0),
            enabled=bool(settings.get("enabled", True)),
        )


class OpenAICompatibleClient:
    """Call a Chat Completions-compatible endpoint with grounded messages."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        if not self.config.available:
            raise LLMError("文字问询连接信息未配置完整")
        endpoint = _chat_endpoint(self.config.endpoint)
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 900,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "FactoryGuard-MerchantConsole/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _redacted_text(read_http_error(exc, limit=4096), self.config.api_key)
            raise LLMError(f"文字问询服务返回 HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = _redacted_text(getattr(exc, "reason", exc), self.config.api_key)
            raise LLMError(f"无法连接文字问询服务: {reason}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError("文字问询服务返回了无法解析的响应") from exc
        content = _extract_content(payload)
        if not content:
            raise LLMError("文字问询服务没有返回可用文本")
        return content.strip()


def _chat_endpoint(value: str) -> str:
    endpoint = value.strip().rstrip("/")
    if not endpoint:
        raise LLMError("文字问询接口地址为空")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return endpoint + "/chat/completions"
    return endpoint + "/v1/chat/completions"


def _redacted_text(value: object, secret: str) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")[-600:]
    return text.replace(secret, "***") if secret else text


def _extract_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                            parts.append(item["text"])
                    if parts:
                        return "\n".join(parts)
            text = first.get("text")
            if isinstance(text, str):
                return text
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            for content in item.get("content", ()) if isinstance(item.get("content"), list) else ():
                if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        if parts:
            return "\n".join(parts)
    return ""


def _positive_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if isfinite(parsed) and parsed > 0 else default


__all__ = ["LLMConfig", "LLMError", "OpenAICompatibleClient"]
