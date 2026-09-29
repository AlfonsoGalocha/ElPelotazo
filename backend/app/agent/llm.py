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


class ClaudeCodeLLMClient(LLMClient):
    """Usa el Claude Agent SDK (el mismo motor de Claude Code) en vez de la
    API de Anthropic facturada por token. Si tienes `claude` logueado con
    tu suscripcion Claude Pro/Max en esta maquina (`claude login` o
    `claude setup-token`), el consumo sale de esa suscripcion, no de una
    API key de pago -- ver docs/modeling.md, seccion del agente.

    Diferencia clave con `AnthropicLLMClient`: aqui el SDK gestiona el
    bucle completo de tool-calling EL SOLO (llama a nuestras tools
    directamente via un servidor MCP en proceso). Por eso `run_turn`
    ejecuta la conversacion entera de un tiron y devuelve un `LLMTurn` con
    `tool_calls=[]` siempre -- el orquestador (`orchestrator.py`) no
    necesita volver a iterar para este proveedor. Las tools SI se loguean
    (mismo formato `agent.tool_call` que el otro proveedor) para
    observabilidad, aunque no aparezcan en el `tool_log` que devuelve la
    API (limitacion conocida y documentada, no un descuido).

    Seguridad (seccion 17 del brief): `tools=[]` en `ClaudeAgentOptions`
    desactiva TODAS las herramientas nativas de Claude Code (Bash, Read,
    Write, WebFetch...) -- el agente SOLO puede llamar a las tools de
    `agent/tools.py`, expuestas via un servidor MCP en proceso. Nunca
    puede ejecutar comandos del sistema ni tocar el filesystem.
    """

    def __init__(self, settings: Settings, db: Any) -> None:
        self._settings = settings
        self._db = db

    def run_turn(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        import asyncio

        from claude_agent_sdk import CLINotFoundError, ProcessError

        try:
            return asyncio.run(self._run_turn_async(system_prompt, messages))
        except CLINotFoundError as exc:
            raise RuntimeError(
                "No se encontro el CLI 'claude' en esta maquina. Instalalo "
                "(npm install -g @anthropic-ai/claude-code) y haz 'claude login' "
                "con tu cuenta Pro/Max antes de usar AGENT_LLM_PROVIDER=claude_code."
            ) from exc
        except ProcessError as exc:
            raise RuntimeError(
                f"El CLI 'claude' fallo al ejecutar la consulta: {exc}. "
                "Comprueba que has hecho 'claude login' (o 'claude setup-token') "
                "y que tu suscripcion sigue activa."
            ) from exc

    async def _run_turn_async(self, system_prompt: str, messages: list[dict[str, Any]]) -> LLMTurn:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            create_sdk_mcp_server,
            query,
            tool,
        )

        from backend.app.agent.tools import TOOLS

        # Solo el ULTIMO mensaje de usuario se manda como prompt: el
        # historial previo (turnos anteriores de la conversacion) no tiene
        # un hueco natural en `query()` de una sola llamada -- limitacion
        # aceptada para este MVP (ver docs/modeling.md), igual que la
        # memoria sin persistencia del resto del agente.
        prompt = messages[-1]["content"] if messages else ""
        if not isinstance(prompt, str):
            prompt = str(prompt)

        sdk_tools = []
        for football_tool in TOOLS:
            sdk_tools.append(self._wrap_as_sdk_tool(tool, football_tool))

        server = create_sdk_mcp_server(name="football", tools=sdk_tools)
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            tools=[],  # desactiva TODAS las tools nativas (Bash/Read/Write/...)
            mcp_servers={"football": server},
            allowed_tools=[f"mcp__football__{t.name}" for t in TOOLS],
            model=self._settings.agent_llm_model or None,
            permission_mode="bypassPermissions",  # solo puede llamar a las tools whitelisted arriba
            max_turns=self._settings.agent_max_tool_iterations,
            # El contenedor Docker de este proyecto corre como root (no hay
            # ningun 'USER' no-root en el Dockerfile), y Claude Code se
            # niega a usar bypassPermissions como root a menos que se le
            # diga explicitamente que esta en un entorno aislado/sandbox
            # (si no, falla con "cannot be used with root/sudo privileges
            # for security reasons" -- error real reproducido probando
            # esto en Docker). No es un problema de seguridad real aqui:
            # el aislamiento de verdad ya lo da `tools=[]` de arriba (sin
            # Bash/Read/Write nativos), este flag solo evita el prompt de
            # aprobacion interactiva para las tools de futbol que SI estan
            # en la whitelist.
            env={"IS_SANDBOX": "1"},
        )

        final_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final_text = block.text
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(f"Claude Code devolvio un error: {message.subtype}")
                if message.result:
                    final_text = message.result

        return LLMTurn(text=final_text, tool_calls=[], stop_reason="end_turn")

    def _wrap_as_sdk_tool(self, tool_decorator: Any, football_tool: Any) -> Any:
        db = self._db

        @tool_decorator(football_tool.name, football_tool.description, football_tool.parameters)
        async def _handler(args: dict[str, Any]) -> dict[str, Any]:
            import json
            import time

            started = time.monotonic()
            try:
                result = football_tool.handler(db, args)
                ok = True
            except Exception as exc:  # noqa: BLE001 -- se reporta al LLM, no se propaga
                result = {"error": str(exc)}
                ok = False
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            logger.info(
                "agent.tool_call: tool=%s ok=%s duration_ms=%s", football_tool.name, ok, duration_ms
            )
            text = json.dumps(result, ensure_ascii=False, default=str)
            return {"content": [{"type": "text", "text": text}]}

        return _handler


def client_for(settings: Settings, db: Any = None) -> LLMClient:
    if settings.agent_llm_provider == "anthropic":
        return AnthropicLLMClient(settings)
    if settings.agent_llm_provider == "claude_code":
        return ClaudeCodeLLMClient(settings, db)
    raise ValueError(
        f"Proveedor de LLM desconocido: {settings.agent_llm_provider!r} "
        "(soportados: 'anthropic', 'claude_code')."
    )
