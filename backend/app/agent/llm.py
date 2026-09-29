"""Cliente LLM abstraido: el orquestador (`orchestrator.py`) programa
contra `LLMClient`, nunca contra el SDK de un proveedor concreto. Cambiar
de proveedor (Anthropic -> OpenAI/Gemini) es anhadir una clase nueva aqui
e implementar `AGENT_LLM_PROVIDER` en `agent_client_for()`, sin tocar el
orquestador ni las tools (seccion 16 del brief: "no acoplar Jarvis a un
unico proveedor").

Solo hay implementacion real para Anthropic de momento (unico proveedor
pedido en la primera fase); las demas se anhaden cuando haga falta.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from backend.app.config.settings import Settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMTurn:
    """Una respuesta del LLM: o bien texto final, o bien una lista de
    tool calls que el orquestador debe ejecutar antes de volver a
    preguntar."""

    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str


class LLMClient(ABC):
    @abstractmethod
    def run_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        """Una unica llamada al LLM. `messages` sigue el formato de turnos
        user/assistant/tool_result que ya usa el orquestador -- cada
        implementacion de proveedor es responsable de traducirlo a su
        propio formato de API."""


class AnthropicLLMClient(LLMClient):
    def __init__(self, settings: Settings) -> None:
        # Import perezoso: el paquete `anthropic` es una dependencia
        # opcional en la practica (solo hace falta si AGENT_ENABLED=true),
        # asi que no debe romper el arranque del resto de la app si no
        # esta instalado en un entorno que no usa el agente.
        import anthropic

        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY no configurada. Ponla en tu .env para usar el agente "
                "(ver AGENT_ENABLED en backend/app/config/settings.py)."
            )
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.agent_llm_model

    def run_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        return LLMTurn(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason,
        )


def client_for(settings: Settings) -> LLMClient:
    if settings.agent_llm_provider == "anthropic":
        return AnthropicLLMClient(settings)
    raise ValueError(
        f"Proveedor de LLM desconocido: {settings.agent_llm_provider!r} "
        "(unico soportado de momento: 'anthropic')."
    )
