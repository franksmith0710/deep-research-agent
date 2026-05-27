from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── 枚举 ──────────────────────────────────────────────────────────────

class Intent(str, Enum):
    deep_research = "deep_research"
    refine_section = "refine_section"
    new_search_topic = "new_search_topic"
    simple_llm = "simple_llm"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    hitl_waiting = "hitl_waiting"
    completed = "completed"
    error = "error"


class ChainType(str, Enum):
    thought = "thought"
    action = "action"
    action_result = "action_result"


class HITLMode(str, Enum):
    scope_select = "scope_select"
    conflict_resolve = "conflict_resolve"
    direction_adjust = "direction_adjust"


class ContextType(str, Enum):
    user_memory = "user_memory"
    session = "session"
    resource = "resource"


# ── SSE 事件 ──────────────────────────────────────────────────────────

class SSEEvent(BaseModel):
    event: str  # chain / text / patch / hitl
    data: str   # JSON 序列化后的 payload

    @classmethod
    def chain(cls, type_: ChainType, node: str, content: str) -> "SSEEvent":
        payload = {
            "type": type_.value,
            "node": node,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        return cls(event="chain", data=json.dumps(payload, ensure_ascii=False))

    @classmethod
    def text(cls, content: str) -> "SSEEvent":
        payload = {"content": content}
        return cls(event="text", data=json.dumps(payload, ensure_ascii=False))

    @classmethod
    def patch(cls, section: str, content: str, append: bool = False) -> "SSEEvent":
        payload = {
            "section": section,
            "content": content,
            "append": append,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        return cls(event="patch", data=json.dumps(payload, ensure_ascii=False))

    @classmethod
    def hitl(cls, mode: HITLMode, session_id: str, options: dict[str, Any] | None = None) -> "SSEEvent":
        payload = {
            "mode": mode.value,
            "session_id": session_id,
            "options": options or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        return cls(event="hitl", data=json.dumps(payload, ensure_ascii=False))


# ── HITL ──────────────────────────────────────────────────────────────

class HITLRequest(BaseModel):
    session_id: str
    mode: HITLMode
    data: dict[str, Any]  # 用户填写的复选 / 文本 / 单选结果


class HITLStatus(BaseModel):
    need_scope: bool = False
    need_adjust: bool = False
    has_conflict: bool = False


# ── 搜索 / 抓取 ───────────────────────────────────────────────────────

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class ScrapedPage(BaseModel):
    url: str
    title: str
    content: str  # L2: trafilatura 提取的 Markdown
    success: bool = True
    error: str | None = None


class PageSummary(BaseModel):
    url: str
    title: str
    topic: str
    key_points_json: str   # 简报：本页关键要点的 JSON 序列化
    page_abstract: str     # 简报：本页概要（首 3 个要点拼接）


# ── 来源信誉 ──────────────────────────────────────────────────────────

class CredibilityInfo(BaseModel):
    url: str
    domain: str
    score: int = 50
    access_count: int = 1
    last_status: str = "success"  # success / fail

    @property
    def tag(self) -> str:
        if self.score >= 80:
            return "高"
        if self.score >= 50:
            return "中"
        return "低"


# ── 记忆 ──────────────────────────────────────────────────────────────

# ── 文件系统 ──────────────────────────────────────────────────────────

class FSNode(BaseModel):
    id: int | None = None
    uri: str
    parent_uri: str | None = None
    is_directory: bool = False
    name: str
    context_type: ContextType = ContextType.user_memory
    level: str | None = None
    content: str | None = None
    abstract: str | None = None
    overview: str | None = None
    embedding: list[float] | None = None
    source_url: str | None = None
    metadata: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── API 请求 / 响应 ──────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str
    session_id: str


class ResearchResponse(BaseModel):
    session_id: str
    task_id: int
    status: TaskStatus


class SessionItem(BaseModel):
    session_id: str
    query_preview: str
    status: TaskStatus
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionItem]


class SessionHistoryResponse(BaseModel):
    messages: list[dict[str, Any]]     # chat_history
    report: str | None = None          # 当前最新完整报告
    report_sections: list[dict[str, str]] = []
