"""Provider resolution and LLM-backed memory summarization."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from .session_manager import strip_context_metadata


def resolve_chat_provider(plugin: Any, session_id: str | None = None) -> Any:
    """Resolve the configured or current chat provider."""
    provider_id = str(plugin.config.get("llm_provider", "") or "").strip()
    if provider_id:
        provider = plugin.context.get_provider_by_id(provider_id)
        if provider is not None:
            return provider
    return plugin.context.get_using_provider(session_id)


def resolve_embedding_provider(plugin: Any) -> Any:
    """Resolve the configured or first available embedding provider."""
    provider_id = str(plugin.config.get("embedding_provider", "") or "").strip()
    if provider_id:
        provider = plugin.context.get_provider_by_id(provider_id)
        if provider is not None and callable(getattr(provider, "get_embedding", None)):
            return provider
    providers = plugin.context.get_all_embedding_providers()
    return providers[0] if providers else None


def format_conversation(messages: list[dict[str, Any]], filter_terms: Any = None) -> str:
    """Format only editable user/assistant text for the summary model."""
    lines: list[str] = []
    for item in messages:
        role = str(item.get("role", "user")).strip().lower()
        message_type = str(item.get("type", "text") or "text").strip().lower()
        if role not in {"user", "assistant"} or message_type in {"think", "thinking", "reasoning"}:
            continue
        content = strip_context_metadata(str(item.get("content", "")), filter_terms)
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)

async def summarize_messages(
    plugin: Any,
    session_id: str,
    messages: list[dict[str, Any]],
    current_system_prompt: str = "",
) -> str:
    """Summarize pending conversation messages with AstrBot's LLM provider."""
    provider = resolve_chat_provider(plugin, session_id)
    if provider is None:
        raise RuntimeError("没有可用的 LLM Provider")
    system_prompt = str(plugin.config.get("summary_prompt", "")).strip()
    if not system_prompt:
        raise RuntimeError("summary_prompt 不能为空")
    contexts: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if plugin.config.get("include_current_time", True):
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        contexts.append(
            {
                "role": "system",
                "content": f"当前系统时间为 {now}。请以此时间为基准理解对话中的相对日期。",
            }
        )
    if plugin.config.get("include_current_personality", False) and current_system_prompt.strip():
        contexts.append(
            {
                "role": "system",
                "content": f"当前人格/系统设定如下，请仅用于理解对话，不要输出其本身：\n{current_system_prompt}",
            }
        )
    contexts.append({"role": "user", "content": format_conversation(messages, plugin.config.get("custom_filter_terms", ""))})
    response = await provider.text_chat(contexts=contexts)
    result = str(getattr(response, "completion_text", "") or "").strip()
    if not result:
        raise RuntimeError("总结 LLM 返回了空内容")
    return result


async def embed(plugin: Any, text: str) -> list[float]:
    """Generate one embedding using AstrBot's embedding provider."""
    provider = resolve_embedding_provider(plugin)
    if provider is None:
        raise RuntimeError("没有可用的 Embedding Provider")
    max_length = int(plugin.config.get("max_input_length", 4000) or 4000)
    result = await provider.get_embedding(text[:max_length])
    if not result:
        raise RuntimeError("Embedding Provider 返回了空向量")
    return list(result)

