from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AgentMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AgentChatRequest(BaseModel):
    message: str
    # El frontend reenvia el historial completo en cada request (memoria
    # de corto plazo SIN persistencia en servidor para el MVP -- ver
    # docs/architecture_audit.md, seccion del agente).
    history: list[AgentMessage] = []


class AgentToolLogEntry(BaseModel):
    tool: str
    ok: bool
    duration_ms: float


class AgentChatResponse(BaseModel):
    reply: str
    tool_log: list[AgentToolLogEntry]


class AgentTranscribeResponse(BaseModel):
    text: str
