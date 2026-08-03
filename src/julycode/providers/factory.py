from __future__ import annotations

from dataclasses import replace

from julycode.config import AppConfig
from julycode.errors import ConfigError
from julycode.providers.anthropic import AnthropicProvider
from julycode.providers.base import LLMProvider
from julycode.providers.openai import OpenAIProvider


def create_provider(config: AppConfig, model_override: str | None = None) -> LLMProvider:
    if model_override and model_override != config.model:
        config = replace(config, model=model_override)
    if config.protocol == "openai":
        return OpenAIProvider(config)
    if config.protocol == "anthropic":
        return AnthropicProvider(config)
    raise ConfigError(f"不支持的 protocol: {config.protocol}")
