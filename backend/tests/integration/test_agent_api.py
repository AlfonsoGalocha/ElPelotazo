"""Tests del endpoint /agent/chat con el LLM MOCKEADO -- nunca se llama a
la API real de Anthropic en la suite de tests (séria lento, de pago, y no
deterministico). Lo que se comprueba es el CABLEADO: autenticacion,
bucle de tool-calling, y que un tool_call real (get_matches_today) se
ejecuta contra la base de datos de verdad."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.agent import orchestrator as orchestrator_module
from backend.app.agent.llm import LLMClient, LLMTurn, ToolCall
from backend.app.config.settings import Settings, get_settings
from backend.app.main import app


class _ScriptedLLMClient(LLMClient):
    """Devuelve, en orden, las respuestas programadas -- simula un LLM que
    primero pide una tool y luego, tras ver el resultado, da texto final."""

    def __init__(self, turns: list[LLMTurn]) -> None:
        self._turns = list(turns)
        self.seen_messages: list[list[dict]] = []

    def run_turn(self, system_prompt, messages, tools):  # noqa: ANN001
        self.seen_messages.append(messages)
        return self._turns.pop(0)


def _settings_with_agent_enabled() -> Settings:
    return Settings(
        agent_enabled=True,
        agent_shared_secret="test-secret",
        anthropic_api_key="unused-because-mocked",
    )


def test_agent_chat_requires_shared_secret():
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post("/agent/chat", json={"message": "hola"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_agent_chat_returns_503_when_disabled():
    app.dependency_overrides[get_settings] = lambda: Settings(agent_enabled=False)
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/chat", json={"message": "hola"}, headers={"X-Agent-Key": "whatever"}
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_agent_chat_runs_real_tool_and_returns_llm_text(monkeypatch, db_session):
    """El LLM (mockeado) pide get_matches_today; el orquestador la ejecuta
    de verdad contra la DB (aqui vacia -> 0 partidos) y se lo devuelve al
    LLM, que entonces da una respuesta final de texto."""
    scripted = _ScriptedLLMClient(
        [
            LLMTurn(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="get_matches_today", arguments={})],
                stop_reason="tool_use",
            ),
            LLMTurn(
                text="No hay partidos programados en la jornada actual de ninguna liga.",
                tool_calls=[],
                stop_reason="end_turn",
            ),
        ]
    )
    monkeypatch.setattr(
        "backend.app.api.routes.agent.client_for", lambda settings, db: scripted
    )
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/chat",
            json={"message": "que partidos hay hoy?"},
            headers={"X-Agent-Key": "test-secret"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["reply"] == "No hay partidos programados en la jornada actual de ninguna liga."
        assert body["tool_log"] == [{"tool": "get_matches_today", "ok": True, "duration_ms": body["tool_log"][0]["duration_ms"]}]
    finally:
        app.dependency_overrides.clear()


def test_agent_chat_reports_unknown_tool_gracefully(monkeypatch):
    scripted = _ScriptedLLMClient(
        [
            LLMTurn(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="tool_que_no_existe", arguments={})],
                stop_reason="tool_use",
            ),
            LLMTurn(text="No pude usar esa herramienta.", tool_calls=[], stop_reason="end_turn"),
        ]
    )
    monkeypatch.setattr(
        "backend.app.api.routes.agent.client_for", lambda settings, db: scripted
    )
    app.dependency_overrides[get_settings] = _settings_with_agent_enabled
    try:
        client = TestClient(app)
        response = client.post(
            "/agent/chat",
            json={"message": "algo raro"},
            headers={"X-Agent-Key": "test-secret"},
        )
        assert response.status_code == 200
        assert response.json()["tool_log"] == [
            {"tool": "tool_que_no_existe", "ok": False, "duration_ms": response.json()["tool_log"][0]["duration_ms"]}
        ]
    finally:
        app.dependency_overrides.clear()


def test_agent_orchestrator_raises_after_max_iterations(monkeypatch, db_session):
    """Si el LLM pide tools indefinidamente sin nunca dar texto final, el
    orquestador debe cortar (nunca un bucle sin techo, seccion 19/21)."""
    settings = Settings(agent_enabled=True, agent_max_tool_iterations=2)
    endless_turn = LLMTurn(
        text=None,
        tool_calls=[ToolCall(id="x", name="get_matches_today", arguments={})],
        stop_reason="tool_use",
    )
    scripted = _ScriptedLLMClient([endless_turn, endless_turn, endless_turn])

    import pytest

    with pytest.raises(orchestrator_module.AgentError):
        orchestrator_module.run_agent_turn(db_session, scripted, settings, "hola")
