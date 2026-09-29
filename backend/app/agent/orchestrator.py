"""Bucle del agente: manda el mensaje del usuario + definicion de tools al
LLM, ejecuta las tools que pida, le devuelve el resultado, y repite hasta
que el LLM de una respuesta de texto final (o se agote el limite de
iteraciones/tiempo, seccion 19 y 21 del brief: nunca un bucle sin techo).

Observabilidad (seccion 18): cada tool ejecutada se loguea con nombre,
duracion y si tuvo exito, SIN loguear los argumentos completos si pudieran
contener datos sensibles (de momento no los hay, pero se deja preparado).
"""

from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from backend.app.agent.llm import LLMClient, ToolCall
from backend.app.agent.tools import TOOLS, TOOLS_BY_NAME, extract_match_references
from backend.app.config.settings import Settings
from backend.app.utils.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """Eres Jarvis, el asistente personal de analisis y prediccion de futbol de \
las 5 grandes ligas europeas (Premier League, LaLiga, Bundesliga, Serie A, Ligue 1) de tu \
unico usuario. Hablas con el de forma natural y cercana, como lo haria un asistente de voz \
de verdad -- no eres un formulario ni un bot que solo entiende comandos exactos.

Conversacion natural:
- Si te saluda ("hola", "que tal", "buenas") o hace charla trivial, responde con naturalidad \
y brevedad antes de, si tiene sentido, ofrecerte a ayudar con algo concreto de futbol -- \
nunca respondas a un saludo como si fuera un error o una peticion fuera de alcance.
- Entiendes preguntas formuladas de forma coloquial o imprecisa ("que tal ve el Madrid- \
Barca", "hay algo interesante hoy", "como pinta la jornada") -- interpreta la intencion mas \
probable y usa las herramientas disponibles en vez de pedir que reformulen con sintaxis \
exacta, salvo que la pregunta sea realmente ambigua entre varias opciones razonables.
- Puedes ofrecer sugerencias proactivas relacionadas (p.ej. "¿quieres que revise si hay \
señales de valor en la jornada?") pero SIEMPRE basadas en llamar a una herramienta real, \
nunca en una opinion general tuya sobre futbol.
- Si te preguntan algo totalmente ajeno al futbol (el tiempo, noticias, cultura general), \
dilo con naturalidad ("de eso no tengo ni idea, soy solo de futbol") en vez de dar una \
respuesta generica de un LLM cualquiera.

Reglas que NUNCA rompes (estas si son innegociables):
1. Nunca inventas datos, partidos, probabilidades ni cuotas. Si una herramienta no te da un \
dato, dices explicitamente que no lo tienes -- nunca rellenas el hueco, tampoco con \
conocimiento futbolistico general no verificado por una herramienta.
2. Nunca presentas una prediccion como una certeza o garantia. Usa siempre lenguaje de \
probabilidad/estimacion ("el modelo estima", "la probabilidad calculada es"), nunca \
"seguro que", "va a ganar" a secas, ni "apuesta segura".
3. Cuando expliques una prediccion, distingue claramente DATOS (lo que devuelve la \
herramienta) de INTERPRETACION (tu resumen en lenguaje natural) -- nunca mezcles ambas \
cosas como si fueran la misma cosa.
4. Si no tienes ninguna herramienta que te de la informacion que te piden (por ejemplo, \
lesiones o alineaciones: todavia no estan disponibles), dilo honestamente en vez de \
responder con conocimiento general sobre futbol.
5. Si una herramienta no encuentra el partido/equipo que te piden, repite UNICAMENTE el \
motivo que ella misma da -- nunca anhadas una razon extra inventada por tu cuenta (como \
afirmar en que liga/division juega un equipo, o cualquier otro dato factual no verificado). \
Un error real: dado el error de 'analyze_match' de 'no se encontro el partido', se anhadio \
"Le Mans juega en Ligue 2" sin que ninguna herramienta lo dijera -- Le Mans SI podia estar en \
una de las 5 ligas cubiertas, el problema era solo que la consulta no se parseo bien, y esa \
frase inventada llevo al usuario a una conclusion falsa.
6. Se conciso. El usuario es un unico usuario tecnico, no necesitas ser formal ni repetir \
disclaimers en cada frase, pero nunca los omitas del todo cuando dictamines algo sobre una \
prediccion en concreto."""


@dataclass
class AgentTurnResult:
    text: str
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    # Partidos mencionados por alguna tool durante este turno (deduplicados
    # por match_id) -- nunca inventados aqui, solo releidos de lo que la
    # tool ya devolvio (ver extract_match_references en agent/tools.py).
    referenced_matches: list[dict[str, Any]] = field(default_factory=list)


class AgentError(Exception):
    """Error del agente pensado para mostrarse tal cual al usuario (nunca
    expone stacktraces ni detalles internos)."""


def _anthropic_tool_specs() -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
        for tool in TOOLS
    ]


def _run_tool_with_timeout(db: Session, call: ToolCall, timeout_seconds: float) -> dict[str, Any]:
    tool = TOOLS_BY_NAME.get(call.name)
    if tool is None:
        return {"error": f"Herramienta desconocida: {call.name!r}."}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(tool.handler, db, call.arguments)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            return {"error": f"La herramienta {call.name!r} tardo demasiado (timeout)."}
        except Exception as exc:  # noqa: BLE001 -- se reporta al LLM, no se propaga tal cual
            logger.error("agent.tool_error: tool=%s error=%s", call.name, exc)
            return {"error": f"La herramienta {call.name!r} fallo: {exc}"}


def run_agent_turn(
    db: Session,
    llm: LLMClient,
    settings: Settings,
    user_message: str,
    history: list[dict[str, Any]] | None = None,
) -> AgentTurnResult:
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    tool_log: list[dict[str, Any]] = []
    tools_spec = _anthropic_tool_specs()
    referenced_matches: list[dict[str, Any]] = []
    seen_match_ids: set[int] = set()

    def _collect_refs(result: dict[str, Any]) -> None:
        for ref in extract_match_references(result):
            if ref["match_id"] not in seen_match_ids:
                seen_match_ids.add(ref["match_id"])
                referenced_matches.append(ref)

    for _ in range(settings.agent_max_tool_iterations):
        turn = llm.run_turn(SYSTEM_PROMPT, messages, tools_spec)
        # ClaudeCodeLLMClient resuelve el bucle de tools EL SOLO (ver
        # llm.py): sus resultados nunca pasan por `_run_tool_with_timeout`
        # de aqui abajo, asi que se recogen de `last_tool_results` (vacio/
        # inexistente para AnthropicLLMClient, que si pasa por ese bucle).
        for result in getattr(llm, "last_tool_results", []):
            _collect_refs(result)

        if not turn.tool_calls:
            return AgentTurnResult(
                text=turn.text or "", tool_log=tool_log, referenced_matches=referenced_matches
            )

        assistant_content: list[dict[str, Any]] = []
        if turn.text:
            assistant_content.append({"type": "text", "text": turn.text})
        for call in turn.tool_calls:
            assistant_content.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            )
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results: list[dict[str, Any]] = []
        for call in turn.tool_calls:
            started = time.monotonic()
            result = _run_tool_with_timeout(db, call, settings.agent_tool_timeout_seconds)
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            ok = "error" not in result
            logger.info(
                "agent.tool_call: tool=%s ok=%s duration_ms=%s", call.name, ok, duration_ms
            )
            tool_log.append({"tool": call.name, "ok": ok, "duration_ms": duration_ms})
            _collect_refs(result)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": call.id, "content": _to_text(result)}
            )
        messages.append({"role": "user", "content": tool_results})

    raise AgentError(
        "No he podido completar la respuesta en un numero razonable de pasos "
        f"(limite: {settings.agent_max_tool_iterations}). Prueba a reformular la pregunta."
    )


def _to_text(result: dict[str, Any]) -> str:
    import json

    return json.dumps(result, ensure_ascii=False, default=str)
