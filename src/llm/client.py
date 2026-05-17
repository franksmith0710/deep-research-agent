from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI
from openai import (
    APIError,
    APIConnectionError,
    RateLimitError,
    APITimeoutError,
)

from src.config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


SYSTEM_PROMPT = """你是一个专业的调研助手，负责帮助用户进行多轮深度研究。
请始终用中文回答，保持客观、准确、结构化的输出。"""


def chat(
    messages: list[dict[str, str]],
    response_format: dict | None = None,
    max_retries: int = 4,
    timeout: float = 60.0,
) -> str:
    """调用 DeepSeek Chat API，自动重试（最多 4 次）。"""
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "timeout": timeout,
        "temperature": 0.3,
    }
    if response_format:
        kwargs["response_format"] = response_format

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(**kwargs)
            if resp.choices:
                return resp.choices[0].message.content or ""
            raise RuntimeError("LLM 返回空响应")
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = min(2 ** attempt + 1, 10)
                time.sleep(wait)
        except APIError as e:
            last_error = e
            if attempt < max_retries - 1 and 500 <= e.status_code < 600:
                time.sleep(min(2 ** attempt + 1, 10))
            else:
                break
        except Exception as e:
            last_error = e
            break

    raise RuntimeError(f"LLM 调用失败（{max_retries} 次重试后）: {last_error}")


def chat_json(
    messages: list[dict[str, str]],
    max_retries: int = 4,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """调用 LLM 并返回 JSON 对象。"""
    text = chat(
        messages=messages,
        response_format={"type": "json_object"},
        max_retries=max_retries,
        timeout=timeout,
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # 如果 response_format 未生效，尝试从文本中提取 JSON
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise RuntimeError(f"LLM 输出非 JSON: {text[:200]}") from e
