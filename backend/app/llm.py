from __future__ import annotations

from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from app.config import settings


def _client() -> OpenAI:
    return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)


def chat_completion(
    messages: list[dict],
    temperature: float = 0.7,
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    timeout: int = 60,
) -> str | None | ChatCompletionMessage:
    """Call DeepSeek.  Pass ``tools`` to enable function-calling; returns
    ``ChatCompletionMessage`` (with ``.tool_calls``) in that mode.
    Without tools, returns ``str | None`` as before."""
    if not settings.deepseek_api_key:
        return None
    kwargs: dict[str, Any] = dict(
        model=settings.llm_model, messages=messages,
        temperature=temperature, timeout=timeout,
    )
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    response = _client().chat.completions.create(**kwargs)
    msg = response.choices[0].message
    return msg if tools else (msg.content or None)


# Backward-compatible alias for code that already imports this
def chat_completion_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float = 0.7,
    tool_choice: str = "auto",
    timeout: int = 60,
) -> ChatCompletionMessage | None:
    return chat_completion(messages, temperature, tools=tools,
                           tool_choice=tool_choice, timeout=timeout)  # type: ignore[return-value]

