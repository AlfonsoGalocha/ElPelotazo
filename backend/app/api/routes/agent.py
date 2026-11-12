"""Endpoint del agente Jarvis. Deliberadamente separado de `/predictions`,
`/matches`, etc: es una capa de ORQUESTACION sobre ellos, no un endpoint de
datos mas.

Seguridad (seccion 17 del brief, uso personal sin multiusuario): un
secreto compartido en la cabecera `X-Agent-Key`, comparado con
`AGENT_SHARED_SECRET`. No es un sistema de autenticacion completo, pero es
el minimo indispensable antes de exponer esto a internet -- sin secreto
configurado, el endpoint responde 503 (no 200 "abierto a cualquiera").
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.agent.llm import client_for
from backend.app.agent.orchestrator import AgentError, run_agent_turn
from backend.app.agent.transcription import TranscriptionError, transcribe_audio
from backend.app.agent.tts import SpeechSynthesisError, synthesize_speech
from backend.app.config.settings import Settings, get_settings
from backend.app.db.database import get_db
from backend.app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentSpeakRequest,
    AgentTranscribeResponse,
)
from backend.app.utils.logging import get_logger

router = APIRouter(prefix="/agent", tags=["agent"])
logger = get_logger(__name__)


def _require_agent_enabled(
    x_agent_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Settings:
    if not settings.agent_enabled or not settings.agent_shared_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "El agente no esta activado. Configura AGENT_ENABLED=true, "
                "ANTHROPIC_API_KEY y AGENT_SHARED_SECRET en tu .env."
            ),
        )
    if x_agent_key != settings.agent_shared_secret:
        raise HTTPException(status_code=401, detail="X-Agent-Key invalida o ausente.")
    return settings


@router.post("/chat", response_model=AgentChatResponse)
def agent_chat(
    payload: AgentChatRequest,
    settings: Settings = Depends(_require_agent_enabled),
    db: Session = Depends(get_db),
) -> AgentChatResponse:
    llm = client_for(settings, db)
    history = [{"role": m.role, "content": m.content} for m in payload.history]
    try:
        result = run_agent_turn(db, llm, settings, payload.message, history)
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Errores de infraestructura del LLM (p.ej. ClaudeCodeLLMClient: CLI
        # ausente, sesion no logueada, token caducado) -- se traducen a un
        # JSON legible en vez de dejar que suban como 500 "pelado" sin
        # cuerpo (lo que ve el cliente HTTP como respuesta vacia).
        logger.error("agent.llm_runtime_error: error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AgentChatResponse(
        reply=result.text, tool_log=result.tool_log, referenced_matches=result.referenced_matches
    )


@router.post("/transcribe", response_model=AgentTranscribeResponse)
async def agent_transcribe(
    audio: UploadFile,
    settings: Settings = Depends(_require_agent_enabled),
) -> AgentTranscribeResponse:
    """Transcribe un clip de voz grabado en el navegador (`MediaRecorder`)
    con Whisper local -- ver backend/app/agent/transcription.py para el por
    que (el reconocimiento nativo del navegador falla en Brave)."""
    audio_bytes = await audio.read()
    suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
    try:
        text = transcribe_audio(audio_bytes, settings, suffix=suffix)
    except TranscriptionError as exc:
        logger.error("agent.transcription_error: error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AgentTranscribeResponse(text=text)


@router.post("/speak")
def agent_speak(
    payload: AgentSpeakRequest,
    settings: Settings = Depends(_require_agent_enabled),
) -> Response:
    """Sintetiza la respuesta de Jarvis a voz con Piper local -- ver
    backend/app/agent/tts.py para el por que (la Web Speech API nativa no
    tiene voces disponibles en Linux/Brave)."""
    try:
        wav_bytes = synthesize_speech(payload.text, settings)
    except SpeechSynthesisError as exc:
        logger.error("agent.speech_synthesis_error: error=%s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=wav_bytes, media_type="audio/wav")
