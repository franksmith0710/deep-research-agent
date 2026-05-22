from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator

from openai import OpenAI, AsyncOpenAI
from openai import (
    APIError,
    APIConnectionError,
    RateLimitError,
    APITimeoutError,
)

from src.config import settings
from src.logging_config import get_logger

logger = get_logger("llm")

_sync_client: OpenAI | None = None
_async_client: AsyncOpenAI | None = None


async def chat_stream(
    messages: list[dict[str, str]],
    model: str = "deepseek-chat",
    max_retries: int = 4,
    timeout: float = 60.0,
) -> AsyncGenerator[str, None]:
    """流式调用 LLM，逐 token 产出。"""
    client = await get_async_client()
    kwargs: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "timeout": timeout,
        "temperature": 0.3,
        "stream": True,
    }

    logger.debug(f"LLM stream start model={settings.deepseek_model} messages={len(messages)}")

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                latency = time.time() - start_time
                token = chunk.choices[0].delta.content or ""
                if token:
                    logger.debug(f"LLM stream token len={len(token)} latency={latency:.3f}s")
                    yield token
                    start_time = time.time()  # reset for next token timing
            return
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            last_error = e
            logger.warning(f"LLM stream retry {attempt + 1}/{max_retries} error={e}")
            if attempt < max_retries - 1:
                wait = min(2 ** attempt + 1, 10)
                await asyncio.sleep(wait)
        except APIError as e:
            last_error = e
            logger.warning(f"LLM stream retry {attempt + 1}/{max_retries} error={e}")
            if attempt < max_retries - 1 and 500 <= e.status_code < 600:
                await asyncio.sleep(min(2 ** attempt + 1, 10))
            else:
                break
        except Exception as e:
            last_error = e
            break

    logger.error(f"LLM stream failed after {max_retries} retries: {last_error}")
    raise RuntimeError(f"LLM 流式调用失败（{max_retries} 次重试后）: {last_error}")


def get_sync_client() -> OpenAI:
    global _sync_client
    if _sync_client is None:
        _sync_client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _sync_client


async def get_async_client() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _async_client


SYSTEM_PROMPT = """你是一个专业的调研助手，负责帮助用户进行多轮深度研究。
请始终用中文回答，保持客观、准确、结构化的输出。"""


def chat(
    messages: list[dict[str, str]],
    response_format: dict | None = None,
    max_retries: int = 4,
    timeout: float = 60.0,
) -> str:
    """调用 DeepSeek Chat API，自动重试（最多 4 次）。"""
    client = get_sync_client()
    kwargs: dict[str, Any] = {
        "model": settings.deepseek_model,
        "messages": messages,
        "timeout": timeout,
        "temperature": 0.3,
    }
    if response_format:
        kwargs["response_format"] = response_format

    logger.debug(f"LLM call model={settings.deepseek_model} messages={len(messages)} timeout={timeout}")

    last_error: Exception | None = None
    for attempt in range(max_retries):
        start_time = time.time()
        try:
            resp = client.chat.completions.create(**kwargs)
            latency = time.time() - start_time
            if resp.choices:
                content = resp.choices[0].message.content or ""
                logger.debug(f"LLM response len={len(content)} latency={latency:.3f}s")
                return content
            raise RuntimeError("LLM 返回空响应")
        except (RateLimitError, APIConnectionError, APITimeoutError) as e:
            last_error = e
            logger.warning(f"LLM retry {attempt + 1}/{max_retries} error={e}")
            if attempt < max_retries - 1:
                wait = min(2 ** attempt + 1, 10)
                time.sleep(wait)
        except APIError as e:
            last_error = e
            logger.warning(f"LLM retry {attempt + 1}/{max_retries} error={e}")
            if attempt < max_retries - 1 and 500 <= e.status_code < 600:
                time.sleep(min(2 ** attempt + 1, 10))
            else:
                break
        except Exception as e:
            last_error = e
            break

    logger.error(f"LLM failed after {max_retries} retries: {last_error}")
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
        logger.warning(f"JSON parse failed, trying regex extraction: {e}")
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise RuntimeError(f"LLM 输出非 JSON: {text[:200]}") from e
