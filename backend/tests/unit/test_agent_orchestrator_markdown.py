"""Bug real reportado por un usuario: 'no para de decir asteriscos' -- el
LLM usaba Markdown (**negrita**, listas con "-"...) pero el chat lo
muestra como texto plano (asteriscos literales) y Piper los lee en voz
alta tal cual. `_strip_markdown` limpia el texto final ademas de la regla
7 del SYSTEM_PROMPT que pide no usar Markdown en primer lugar."""

from __future__ import annotations

import pytest

from backend.app.agent.llm import LLMClient, LLMTurn
from backend.app.agent.orchestrator import _strip_markdown, run_agent_turn
from backend.app.config.settings import Settings


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("**Hola** que tal", "Hola que tal"),
        ("- item uno\n- item dos", "item uno\nitem dos"),
        ("# Titulo\ntexto", "Titulo\ntexto"),
        ("`over_2_5`", "over_2_5"),
        ("*italic*", "italic"),
        (
            "El equipo **Real Madrid** juega hoy con `ventaja`",
            "El equipo Real Madrid juega hoy con ventaja",
        ),
        # Regresion real: el guion bajo NO se trata como cursiva -- un
        # nombre de mercado snake_case no debe perder caracteres.
        ("La señal es sobre `over_2_5` con edge del 5%", "La señal es sobre over_2_5 con edge del 5%"),
        ("Sin formato, texto normal.", "Sin formato, texto normal."),
    ],
)
def test_strip_markdown_removes_common_markdown_syntax(raw, expected):
    assert _strip_markdown(raw) == expected


def test_run_agent_turn_strips_markdown_from_final_text(db_session):
    class _MarkdownLLMClient(LLMClient):
        def run_turn(self, system_prompt, messages, tools):  # noqa: ANN001
            return LLMTurn(
                text="El **Real Madrid** juega hoy:\n- Partido a las 20:00",
                tool_calls=[],
                stop_reason="end_turn",
            )

    settings = Settings(agent_enabled=True)
    result = run_agent_turn(db_session, _MarkdownLLMClient(), settings, "hola")

    assert "*" not in result.text
    assert "El Real Madrid juega hoy" in result.text
