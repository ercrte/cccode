from __future__ import annotations

import re


class MewCodeError(Exception):
    """MewCode 可展示给用户的基础错误。"""


class ConfigError(MewCodeError):
    """配置加载或校验失败。"""


class ProviderError(MewCodeError):
    """模型供应商请求或流式解析失败。"""


_COMMON_SECRET_RE = re.compile(r"(?i)\b(sk-[a-z0-9_-]{8,}|[a-z0-9_-]{32,})\b")


def redact_secret(text: str, secret: str | None = None) -> str:
    """从错误文本中移除完整密钥。"""
    redacted = text
    if secret:
        redacted = redacted.replace(secret, "[REDACTED]")
    return _COMMON_SECRET_RE.sub("[REDACTED]", redacted)

