"""SSE 事件管理器：推送 4 种 event 到前端。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from sse_starlette.sse import EventSourceResponse

from src.models import ChainType, HITLMode


class SSEManager:
    """管理单次 research 请求的 SSE 事件流。"""
    
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def put_event(self, event: str, data: dict[str, Any]) -> None:
        data["ts"] = datetime.now(timezone.utc).isoformat()
        await self._queue.put({"event": event, "data": json.dumps(data, ensure_ascii=False)})

    async def put_chain(self, type_: ChainType | str, node: str, content: str) -> None:
        await self.put_event("chain", {
            "type": type_.value if isinstance(type_, ChainType) else type_,
            "node": node,
            "content": content,
        })

    async def put_text(self, content: str) -> None:
        await self.put_event("text", {"content": content})

    async def put_patch(self, section: str, content: str, append: bool = False) -> None:
        await self.put_event("patch", {
            "section": section,
            "content": content,
            "append": append,
        })

    async def put_hitl(self, mode: HITLMode | str, session_id: str, options: dict | None = None) -> None:
        await self.put_event("hitl", {
            "mode": mode.value if isinstance(mode, HITLMode) else mode,
            "session_id": session_id,
            "options": options or {},
        })

    async def put_done(self) -> None:
        await self._queue.put(None)

    async def event_generator(self) -> AsyncGenerator[dict[str, Any], None]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item

    def get_response(self) -> EventSourceResponse:
        return EventSourceResponse(self.event_generator())
