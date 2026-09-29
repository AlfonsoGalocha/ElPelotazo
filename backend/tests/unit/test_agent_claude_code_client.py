"""Tests de ClaudeCodeLLMClient con el Claude Agent SDK MOCKEADO -- nunca
se invoca el CLI `claude` real (no hay sesion Pro/Max logueada en la
suite de tests, y aunque la hubiera, seria lento/no determinista).

Lo que se comprueba: que el wrapper llama de verdad a nuestro
`get_matches_today` real via la tool envuelta, y que el texto final
devuelto por el SDK (via ResultMessage.result) llega intacto como
LLMTurn.text con tool_calls=[] (el bucle de tools lo resuelve el SDK, no
el orquestador -- ver docs/modeling.md)."""

from __future__ import annotations

import sys
import types

import pytest


def _install_fake_claude_agent_sdk(monkeypatch, *, final_result: str, tool_should_be_called: bool):
    """Construye un modulo `claude_agent_sdk` falso minimo, solo con lo que
    `ClaudeCodeLLMClient` importa, y lo registra en sys.modules ANTES de que
    el codigo bajo test haga su `import claude_agent_sdk` perezoso."""

    called = {"tool_invoked": False}

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content: list) -> None:
            self.content = content

    class FakeResultMessage:
        def __init__(self, result: str, is_error: bool = False, subtype: str = "success") -> None:
            self.result = result
            self.is_error = is_error
            self.subtype = subtype

    class FakeCLINotFoundError(Exception):
        pass

    class FakeProcessError(Exception):
        pass

    def fake_tool(name, description, input_schema):  # noqa: ANN001
        def decorator(fn):
            async def wrapper(args):
                called["tool_invoked"] = True
                return await fn(args)

            wrapper.tool_name = name
            return wrapper

        return decorator

    def fake_create_sdk_mcp_server(name, tools=None, version="1.0.0"):  # noqa: ANN001
        return {"name": name, "tools": tools or []}

    class FakeClaudeAgentOptions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    async def fake_query(*, prompt, options):  # noqa: ANN001
        # Simula que el SDK, internamente, invoca la primera tool
        # configurada (como haria Claude Code al decidir usarla) antes de
        # dar el texto final -- asi comprobamos que nuestro wrapper
        # realmente ejecuta el handler real.
        if tool_should_be_called:
            for tool_fn in options.kwargs["mcp_servers"]["football"]["tools"]:
                await tool_fn({})
        yield FakeAssistantMessage([FakeTextBlock(final_result)])
        yield FakeResultMessage(result=final_result)

    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.AssistantMessage = FakeAssistantMessage
    fake_module.TextBlock = FakeTextBlock
    fake_module.ResultMessage = FakeResultMessage
    fake_module.ClaudeAgentOptions = FakeClaudeAgentOptions
    fake_module.create_sdk_mcp_server = fake_create_sdk_mcp_server
    fake_module.tool = fake_tool
    fake_module.query = fake_query
    fake_module.CLINotFoundError = FakeCLINotFoundError
    fake_module.ProcessError = FakeProcessError

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)
    return called


def test_claude_code_client_runs_real_tool_and_returns_sdk_final_text(monkeypatch, db_session):
    from backend.app.agent.llm import ClaudeCodeLLMClient
    from backend.app.config.settings import Settings

    called = _install_fake_claude_agent_sdk(
        monkeypatch,
        final_result="No hay partidos programados en la jornada actual.",
        tool_should_be_called=True,
    )

    settings = Settings(agent_llm_provider="claude_code", agent_llm_model="claude-sonnet-5")
    client = ClaudeCodeLLMClient(settings, db_session)

    turn = client.run_turn("system prompt", [{"role": "user", "content": "que partidos hay hoy?"}], [])

    assert called["tool_invoked"] is True
    assert turn.text == "No hay partidos programados en la jornada actual."
    assert turn.tool_calls == []
    assert turn.stop_reason == "end_turn"


def test_claude_code_client_raises_clear_error_when_cli_missing(monkeypatch, db_session):
    from backend.app.agent.llm import ClaudeCodeLLMClient
    from backend.app.config.settings import Settings

    _install_fake_claude_agent_sdk(monkeypatch, final_result="", tool_should_be_called=False)
    import claude_agent_sdk as fake_sdk

    async def raising_query(*, prompt, options):  # noqa: ANN001
        raise fake_sdk.CLINotFoundError("claude CLI not found")
        yield  # pragma: no cover -- hace de esta funcion un generador

    monkeypatch.setattr(fake_sdk, "query", raising_query)

    settings = Settings(agent_llm_provider="claude_code")
    client = ClaudeCodeLLMClient(settings, db_session)

    with pytest.raises(RuntimeError, match="claude"):
        client.run_turn("system prompt", [{"role": "user", "content": "hola"}], [])
