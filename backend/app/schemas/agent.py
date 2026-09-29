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


class AgentMatchReference(BaseModel):
    match_id: int
    home_team: str
    away_team: str


class AgentChatResponse(BaseModel):
    reply: str
    tool_log: list[AgentToolLogEntry]
    # Partidos que alguna tool menciono durante este turno (deduplicados),
    # para que el frontend pueda ofrecer un enlace directo -- pedido real
    # de usuario ("muestrame el partido X"). Nunca inventado: solo
    # releido de lo que la tool ya devolvio (agent/tools.py::extract_match_references).
    referenced_matches: list[AgentMatchReference] = []


class AgentTranscribeResponse(BaseModel):
    text: str


class AgentSpeakRequest(BaseModel):
    text: str
